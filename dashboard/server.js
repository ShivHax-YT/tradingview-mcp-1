import express from 'express';
import { spawn } from 'child_process';
import { writeFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

import { connect as createConnection } from '../src/connection.js';
import { getQuote, getOhlcv } from '../src/core/data.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname  = dirname(__filename);
const ROOT       = resolve(__dirname, '..');
const PROMPT_FILE = resolve(__dirname, 'current-scan.txt');

let currentSymbol = 'MNQ1!';

const app = express();
app.use(express.json());
app.use(express.static(__dirname));

// ── SSE clients ──────────────────────────────────────────────────────────────

const clients = new Set();

function broadcast(event, data) {
  const msg = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const res of clients) {
    try { res.write(msg); } catch { clients.delete(res); }
  }
}

// ── Constants ─────────────────────────────────────────────────────────────────

const TICK_SIZE           = 0.25;
const MISS_TICKS          = 15;
const MISS_DIST           = MISS_TICKS * TICK_SIZE;   // 3.75 pts
const CLAUDE_TIMEOUT      = 3 * 60_000;
const QUOTE_INTERVAL      = 3_000;
const PROXIMITY_THRESHOLD = 50;                        // pts — trigger scan
const PROXIMITY_CLEAR     = 60;                        // pts — clear near_level
let   SCAN_COOLDOWN       = 10 * 60_000;              // 10 min per level (overrideable via POST /cooldown)

// ── Symbol helpers ────────────────────────────────────────────────────────────

function isCrypto(sym) {
  return sym.includes('USDT') || sym.startsWith('BINANCE:');
}

function getProximityThreshold() {
  return isCrypto(currentSymbol) ? 500 : PROXIMITY_THRESHOLD;
}

function getProximityClear() {
  return isCrypto(currentSymbol) ? 600 : PROXIMITY_CLEAR;
}

// For crypto there is no tick size constraint — use 0.5% of price as miss distance.
function getMissDist(price) {
  return isCrypto(currentSymbol) ? price * 0.005 : MISS_DIST;
}

// ── Prompts ───────────────────────────────────────────────────────────────────

const MCP_PREFIX = 'You have TradingView MCP tools available. Use them now. ';

function buildProximityPrompt(levelName, levelPrice, currentPrice) {
  return MCP_PREFIX +
    `${currentSymbol} price is approaching ${levelName} at ${levelPrice}. ` +
    `Current price: ${currentPrice}. ` +
    'Quick check — skip screenshot, summary:true, max 15 bars: ' +
    `1) Was ${levelName} swept in last 5 bars? Yes/No ` +
    '2) If swept: did price reclaim within 3 candles? Yes/No ' +
    '3) If reclaimed: is 5m CHoCH/MSS confirmed? Yes/No ' +
    '4) If confirmed: is FVG entry within 15pts of current price? ' +
    '5) R:R at least 1.5R to nearest liquidity? ' +
    'If all yes: DIRECTION:LONG/SHORT ZONE:X-X STOP:X TP1:X TP2:X ' +
    'If any no: SIGNAL:WAIT — [which condition failed] ' +
    'Write your brief analysis, then end your text with a JSON block in this exact format:\n' +
    '```json\n' +
    '{"decision":"LONG|SHORT|WAIT","reason_code":"short_snake_case","setup_state":"untouched|swept_no_reclaim|reclaimed_no_confirmation|confirmed","zone":[low,high],"stop":number,"tp1":number,"tp2":number,"invalidate_if":"condition text"}\n' +
    '```\n' +
    'The JSON block must be the final text in your response. After the JSON, call chart_set_timeframe to restore to 15m as your final action.';
}

// ── TradingView persistent connection ─────────────────────────────────────────

let tvConnected = false;

async function initConnection() {
  try {
    await createConnection();
    tvConnected = true;
    console.log('TradingView connected for price watching');
    broadcast('tv_status', { connected: true });
  } catch (e) {
    tvConnected = false;
    console.log(`TradingView not reachable (${e.message}), retrying in 10s`);
    broadcast('tv_status', { connected: false });
    setTimeout(initConnection, 10_000);
  }
}

// ── State machine ─────────────────────────────────────────────────────────────

const st = {
  name:           'HUNTING',
  mode:           'swing',
  running:        true,
  signal:         null,
  entry:          null,
  lastPrice:      null,
  claudeOutput:   '',
  scanLog:        [],
  proximityTimer: null,
  quoteTimer:     null,
  claudeProc:     null,
  claudeTimeout:  null,
  scanCount:      0,
  scanStartedAt:  null,
  scanFinishedAt: null,
  scanDuration:   null,
  sessionLevels:  null,
  sessionLoading:       false,
  nearLevel:            null,
  scannedLevels:        new Map(),
  sessionRefreshTimer:  null,
  sessionBoundaryTimer: null,
};

let proximityRunning = false;

// ── Helpers ───────────────────────────────────────────────────────────────────

function addToLog(entry) {
  const time = new Date().toLocaleTimeString('en-US', { hour12: false, timeZone: 'America/Los_Angeles' });
  st.scanLog.unshift({
    ...entry,
    time,
    scanStart:  st.scanStartedAt,
    scanEnd:    st.scanFinishedAt,
    duration:   st.scanDuration,
    stale:      entry.stale || false,
  });
  if (st.scanLog.length > 10) st.scanLog.pop();
  broadcast('signal_log', { log: st.scanLog });
}

function clearTimers() {
  if (st.proximityTimer)     { clearInterval(st.proximityTimer);     st.proximityTimer = null; }
  if (st.quoteTimer)         { clearInterval(st.quoteTimer);         st.quoteTimer = null; }
  if (st.claudeTimeout)      { clearTimeout(st.claudeTimeout);       st.claudeTimeout = null; }
  if (st.claudeProc)         { try { st.claudeProc.kill('SIGTERM'); } catch {} st.claudeProc = null; }
  if (st.sessionRefreshTimer)  { clearInterval(st.sessionRefreshTimer);  st.sessionRefreshTimer = null; }
  if (st.sessionBoundaryTimer) { clearInterval(st.sessionBoundaryTimer); st.sessionBoundaryTimer = null; }
}

function parseSignal(text) {
  const dirMatch = text.match(/DIRECTION:(LONG|SHORT)/i);
  if (!dirMatch) return null;
  const direction = dirMatch[1].toUpperCase();

  const zoneMatch = text.match(/ZONE:(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)/i);
  const stopMatch = text.match(/STOP:(\d+(?:\.\d+)?)/i);
  const tp1Match  = text.match(/TP1:(\d+(?:\.\d+)?)/i);
  const tp2Match  = text.match(/TP2:(\d+(?:\.\d+)?)/i);
  if (!zoneMatch || !stopMatch || !tp1Match || !tp2Match) return null;

  const zone    = [parseFloat(zoneMatch[1]), parseFloat(zoneMatch[2])];
  const stop    = parseFloat(stopMatch[1]);
  const tp1     = parseFloat(tp1Match[1]);
  const tp2     = parseFloat(tp2Match[1]);
  const zoneAvg = (zone[0] + zone[1]) / 2;
  const risk    = direction === 'LONG' ? zoneAvg - stop : stop - zoneAvg;
  const reward  = direction === 'LONG' ? tp1 - zoneAvg : zoneAvg - tp1;
  const rr      = risk > 0 ? (reward / risk).toFixed(2) : 'N/A';

  return { direction, zone, stop, tp1, tp2, rr };
}

function parseSignalJSON(text) {
  let jsonStr = null;
  const fenceMatches = [...text.matchAll(/```json\s*([\s\S]*?)```/gi)];
  if (fenceMatches.length > 0) {
    jsonStr = fenceMatches[fenceMatches.length - 1][1];
  } else {
    const lastBrace = text.lastIndexOf('}');
    if (lastBrace !== -1) {
      const firstBrace = text.lastIndexOf('{', lastBrace);
      if (firstBrace !== -1) jsonStr = text.slice(firstBrace, lastBrace + 1);
    }
  }
  if (!jsonStr) return null;

  try {
    const obj = JSON.parse(jsonStr);
    const decision = (obj.decision || '').toUpperCase();
    if (decision === 'WAIT') return { wait: true };
    if ((decision === 'LONG' || decision === 'SHORT') &&
        Array.isArray(obj.zone) && obj.zone.length === 2 &&
        typeof obj.stop === 'number' && typeof obj.tp1 === 'number' && typeof obj.tp2 === 'number') {
      const direction = decision;
      const zone      = obj.zone.map(Number);
      const stop      = obj.stop;
      const tp1       = obj.tp1;
      const tp2       = obj.tp2;
      const zoneAvg   = (zone[0] + zone[1]) / 2;
      const risk      = direction === 'LONG' ? zoneAvg - stop : stop - zoneAvg;
      const reward    = direction === 'LONG' ? tp1 - zoneAvg : zoneAvg - tp1;
      const rr        = risk > 0 ? (reward / risk).toFixed(2) : 'N/A';
      return { direction, zone, stop, tp1, tp2, rr };
    }
    return null;
  } catch {
    return null;
  }
}

function getAllLevels() {
  if (!st.sessionLevels) return [];
  const { asiaHigh, asiaLow, londonHigh, londonLow, pdHigh, pdLow, nyHigh, nyLow } = st.sessionLevels;
  return [
    { name: 'Asia High',   price: asiaHigh },
    { name: 'Asia Low',    price: asiaLow },
    { name: 'London High', price: londonHigh },
    { name: 'London Low',  price: londonLow },
    { name: 'PD High',     price: pdHigh },
    { name: 'PD Low',      price: pdLow },
    { name: 'NY High',     price: nyHigh },
    { name: 'NY Low',      price: nyLow },
  ].filter(l => l.price !== null);
}

// ── Tier 1: Session level loader (runs ONCE on startup / session change) ──────

async function loadSessionLevels() {
  if (st.sessionLoading) return;
  st.sessionLoading = true;
  broadcast('session_loading', { message: 'Loading session levels...' });
  console.log('Loading session levels via OHLCV...');

  try {
    const result = await getOhlcv({ count: 96 }); // 96 × 15m = 24 hours
    console.log('OHLCV result keys:', Object.keys(result));
    const bars   = result.bars;
    console.log('First bar:', bars?.[0]);

    // Helpers: convert Unix seconds → PST hour / date string
    function toPSTHour(unixSec) {
      const s = new Date(unixSec * 1000).toLocaleString('en-US', {
        timeZone: 'America/Los_Angeles', hour: 'numeric', hour12: false,
      });
      return parseInt(s, 10); // 0-23
    }
    function toPSTDate(unixSec) {
      return new Date(unixSec * 1000).toLocaleDateString('en-US', { timeZone: 'America/Los_Angeles' });
    }

    const todayPST     = new Date().toLocaleDateString('en-US', { timeZone: 'America/Los_Angeles' });
    const yesterdayPST = new Date(Date.now() - 86_400_000).toLocaleDateString('en-US', { timeZone: 'America/Los_Angeles' });

    const asiaH = [], asiaL = [], londonH = [], londonL = [], pdH = [], pdL = [], nyH = [], nyL = [];

    for (const bar of bars) {
      const hour = toPSTHour(bar.time);
      const date = toPSTDate(bar.time);

      // Asia  : 18:00-23:00 PST (6 PM – 11 PM)
      if (hour >= 18 && hour <= 23) { asiaH.push(bar.high); asiaL.push(bar.low); }

      // London: 22:00-01:00 PST (10 PM – 1 AM, wraps midnight)
      if (hour >= 22 || hour <= 1)  { londonH.push(bar.high); londonL.push(bar.low); }

      // Prior day: every bar from yesterday's PST date
      if (date === yesterdayPST)    { pdH.push(bar.high); pdL.push(bar.low); }

      // NY    : 06:00-11:00 PST (6 AM – 11 AM)
      if (hour >= 6 && hour <= 11)  { nyH.push(bar.high); nyL.push(bar.low); }
    }

    const levels = {
      asiaHigh:   asiaH.length   ? Math.max(...asiaH)   : null,
      asiaLow:    asiaL.length   ? Math.min(...asiaL)   : null,
      londonHigh: londonH.length ? Math.max(...londonH) : null,
      londonLow:  londonL.length ? Math.min(...londonL) : null,
      pdHigh:     pdH.length     ? Math.max(...pdH)     : null,
      pdLow:      pdL.length     ? Math.min(...pdL)     : null,
      nyHigh:     nyH.length     ? Math.max(...nyH)     : null,
      nyLow:      nyL.length     ? Math.min(...nyL)     : null,
    };

    st.sessionLoading = false;
    const valid = Object.values(levels).some(v => v !== null);
    if (valid) {
      st.sessionLevels = levels;
      broadcast('session_levels', { levels });
      console.log('Session levels loaded:', levels);
    } else {
      broadcast('error', { message: 'No session levels found in OHLCV data' });
      console.warn('Session levels: no bars matched any session window');
    }
  } catch (e) {
    st.sessionLoading = false;
    console.warn(`Session levels failed (${e.message}), retrying in 10s`);
    broadcast('session_loading', { message: `Retrying session levels... (${e.message})` });
    setTimeout(loadSessionLevels, 10_000);
  }
}

// ── Tier 3: Targeted proximity scan ──────────────────────────────────────────

function runProximityScan(levelName, levelPrice) {
  if (st.name !== 'HUNTING') return;
  if (st.claudeProc) return;

  st.claudeOutput   = '';
  st.scanStartedAt  = Date.now();
  st.scanFinishedAt = null;
  st.scanDuration   = null;
  st.scanCount++;
  broadcast('scan_started', { time: new Date().toISOString(), count: st.scanCount, level: levelName });
  console.log(`[SCAN] Starting scan for "${levelName}", SSE clients connected: ${clients.size}`);
  addToLog({ direction: levelName, result: 'SCANNING' });

  const prompt = buildProximityPrompt(levelName, levelPrice, st.lastPrice);

  try {
    writeFileSync(PROMPT_FILE, prompt, 'utf8');
  } catch (e) {
    broadcast('error', { message: `Failed to write prompt file: ${e.message}` });
    return;
  }

  const child = spawn('cmd', ['/c', 'type dashboard\\current-scan.txt | claude'], {
    stdio: ['ignore', 'pipe', 'pipe'],
    cwd:   ROOT,
    shell: true,
  });

  st.claudeProc = child;

  st.claudeTimeout = setTimeout(() => {
    if (st.claudeProc !== child) return;
    broadcast('error', { message: 'Claude scan timed out (3 min)' });
    try { child.kill('SIGTERM'); } catch {}
    st.claudeProc    = null;
    st.claudeTimeout = null;
  }, CLAUDE_TIMEOUT);

  child.stdout.on('data', (chunk) => {
    const token = chunk.toString();
    st.claudeOutput += token;
    console.log(`[TOKEN] ${clients.size} clients | ${token.substring(0, 80).replace(/\n/g, '\\n')}`);
    broadcast('claude_token', { token });
  });

  child.stderr.on('data', (chunk) => {
    const text = chunk.toString().trim();
    if (text) broadcast('claude_token', { token: text, isErr: true });
  });

  child.on('close', () => {
    clearTimeout(st.claudeTimeout);
    st.claudeTimeout = null;
    if (st.claudeProc === child) st.claudeProc = null;

    st.scanFinishedAt = Date.now();
    st.scanDuration   = st.scanFinishedAt - (st.scanStartedAt ?? st.scanFinishedAt);
    broadcast('scan_finished', { duration: st.scanDuration, time: new Date().toISOString() });

    // Replace the SCANNING placeholder with the real result
    const scanIdx = st.scanLog.findIndex(e => e.result === 'SCANNING');
    if (scanIdx !== -1) st.scanLog.splice(scanIdx, 1);

    const STALE_DIST = 20;

    function applySignal(signal) {
      const price = st.lastPrice;
      let stale = false;
      if (price !== null) {
        const [zoneMin, zoneMax] = signal.zone;
        if (signal.direction === 'SHORT' && price < zoneMin - STALE_DIST) stale = true;
        else if (signal.direction === 'LONG'  && price > zoneMax + STALE_DIST) stale = true;
      }
      if (stale) {
        addToLog({ direction: signal.direction, result: 'MISSED', zone: signal.zone, rr: signal.rr, stale: true });
        broadcast('signal_missed', { reason: 'Signal stale — price moved past zone during scan', signal, price });
        broadcast('scan_complete', { result: 'WAIT' });
      } else {
        addToLog({ direction: signal.direction, result: 'ARMED', zone: signal.zone, rr: signal.rr });
        toArmed(signal);
      }
    }

    const jsonResult = parseSignalJSON(st.claudeOutput);
    if (jsonResult && jsonResult.wait) {
      addToLog({ direction: 'WAIT', result: 'WAIT' });
      broadcast('scan_complete', { result: 'WAIT' });
    } else if (jsonResult) {
      applySignal(jsonResult);
    } else {
      // Fallback: regex parser
      if (/SIGNAL:\s*WAIT/i.test(st.claudeOutput)) {
        addToLog({ direction: 'WAIT', result: 'WAIT' });
        broadcast('scan_complete', { result: 'WAIT' });
      } else {
        const signal = parseSignal(st.claudeOutput);
        if (signal) {
          applySignal(signal);
        } else {
          addToLog({ direction: 'WAIT', result: 'WAIT' });
          broadcast('scan_complete', { result: 'WAIT' });
        }
      }
    }
  });

  child.on('error', (err) => {
    clearTimeout(st.claudeTimeout);
    st.claudeTimeout = null;
    st.claudeProc    = null;
    broadcast('error', { message: `Claude spawn error: ${err.message}` });
  });
}

// ── Tier 2: Price proximity watcher (every 3s, zero Claude) ──────────────────

async function preFilterSetup(level) {
  try {
    const result = await getOhlcv({ count: 6 });
    const bars = result.bars;
    if (!bars || bars.length === 0) return { pass: true, reason: 'prefilter unavailable, allowing scan' };
    const checkBars = bars.slice(-5);
    const isHigh = level.name.includes('High');
    const isLow  = level.name.includes('Low');
    if (isHigh) {
      const swept = checkBars.some(b => b.high > level.price);
      return swept
        ? { pass: true,  reason: 'sweep detected' }
        : { pass: false, reason: 'no sweep in last 5 bars' };
    }
    if (isLow) {
      const swept = checkBars.some(b => b.low < level.price);
      return swept
        ? { pass: true,  reason: 'sweep detected' }
        : { pass: false, reason: 'no sweep in last 5 bars' };
    }
    return { pass: true, reason: 'level type unknown, allowing scan' };
  } catch {
    return { pass: true, reason: 'prefilter unavailable, allowing scan' };
  }
}

async function checkProximity() {
  if (proximityRunning) return;
  if (!st.running) return;
  if (st.lastPrice === null) return;
  if (!st.sessionLevels) return;

  // Dead Zone: futures markets are closed on weekends so there is nothing to scan.
  // Crypto trades 24/7, so bypass this block entirely for crypto symbols.
  if (!isCrypto(currentSymbol)) {
    const day = new Date().getDay(); // 0 = Sunday, 6 = Saturday
    if (day === 0 || day === 6) return;
  }

  const price  = st.lastPrice;
  const levels = getAllLevels();

  let nearestLevel = null;
  let nearestDist  = Infinity;

  for (const level of levels) {
    const dist = Math.abs(price - level.price);
    if (dist <= getProximityThreshold() && dist < nearestDist) {
      nearestDist  = dist;
      nearestLevel = level;
    }
  }

  if (nearestLevel) {
    st.nearLevel = { name: nearestLevel.name, price: nearestLevel.price };
    broadcast('near_level', {
      level:        nearestLevel.name,
      levelPrice:   nearestLevel.price,
      currentPrice: price,
      dist:         Math.round(nearestDist * 100) / 100,
    });

    if (st.name === 'HUNTING' && !st.claudeProc && !st.sessionLoading) {
      const lastScanned = st.scannedLevels.get(nearestLevel.name);
      if (!lastScanned || Date.now() - lastScanned >= SCAN_COOLDOWN) {
        proximityRunning = true;
        try {
          const preResult = await preFilterSetup(nearestLevel);
          if (!preResult.pass) {
            console.log(`[PREFILTER] Skipped scan for ${nearestLevel.name} — ${preResult.reason}`);
            broadcast('prefilter_skip', { level: nearestLevel.name, reason: preResult.reason });
            st.scannedLevels.set(nearestLevel.name, Date.now());
          } else {
            st.scannedLevels.set(nearestLevel.name, Date.now());
            runProximityScan(nearestLevel.name, nearestLevel.price);
          }
        } finally {
          proximityRunning = false;
        }
      }
    }
  } else {
    const allFar = levels.every(l => Math.abs(price - l.price) > getProximityClear());
    if (allFar && st.nearLevel) {
      st.nearLevel = null;
      broadcast('level_clear', {});
    }
  }
}

// ── Quote polling ─────────────────────────────────────────────────────────────

async function pollQuote() {
  if (!st.running) return;
  if (!tvConnected) return;
  try {
    const result = await getQuote({});
    const price  = result.last ?? result.close ?? result.price;
    if (price != null) {
      st.lastPrice = price;
      handlePrice(price);
    }
  } catch (e) {
    tvConnected = false;
    broadcast('tv_status', { connected: false });
    setTimeout(initConnection, 5_000);
  }
}

// ── Price handler ─────────────────────────────────────────────────────────────

function handlePrice(price) {
  if (st.name === 'ARMED') {
    const { signal } = st;
    const [zoneMin, zoneMax] = signal.zone;

    broadcast('price_update', {
      price,
      stateCtx:  'ARMED',
      zone:      signal.zone,
      direction: signal.direction,
      stop:      signal.stop,
      tp1:       signal.tp1,
    });

    if (price >= zoneMin && price <= zoneMax) {
      addToLog({ direction: signal.direction, result: 'LIVE', zone: signal.zone });
      toLive();
    } else if (signal.direction === 'LONG' && price < zoneMin - getMissDist(price)) {
      addToLog({ direction: signal.direction, result: 'MISSED' });
      toHunting('MISSED');
    } else if (signal.direction === 'SHORT' && price > zoneMax + getMissDist(price)) {
      addToLog({ direction: signal.direction, result: 'MISSED' });
      toHunting('MISSED');
    } else if (signal.direction === 'LONG' && price < signal.stop) {
      addToLog({ direction: signal.direction, result: 'INVALID' });
      toHunting('INVALID');
    } else if (signal.direction === 'SHORT' && price > signal.stop) {
      addToLog({ direction: signal.direction, result: 'INVALID' });
      toHunting('INVALID');
    }

  } else if (st.name === 'LIVE') {
    const { signal, entry } = st;
    const pnl     = signal.direction === 'LONG' ? price - entry : entry - price;
    const distTp1 = signal.direction === 'LONG' ? signal.tp1 - price : price - signal.tp1;
    const distTp2 = signal.direction === 'LONG' ? signal.tp2 - price : price - signal.tp2;

    broadcast('price_update', {
      price,
      stateCtx: 'LIVE',
      entry,
      pnl:      +pnl.toFixed(2),
      distTp1:  +distTp1.toFixed(2),
      distTp2:  +distTp2.toFixed(2),
      direction: signal.direction,
    });

  } else if (st.name === 'HUNTING') {
    broadcast('price_update', { price, stateCtx: 'HUNTING' });
  }
}

// ── State transitions ─────────────────────────────────────────────────────────

function toArmed(signal) {
  clearTimers();
  st.name      = 'ARMED';
  st.signal    = signal;
  st.nearLevel = null;
  broadcast('state_change', { state: 'ARMED', signal });
  broadcast('level_clear', {});
  st.quoteTimer = setInterval(pollQuote, QUOTE_INTERVAL);
}

function toLive() {
  st.name  = 'LIVE';
  st.entry = st.lastPrice;
  broadcast('state_change', { state: 'LIVE', signal: st.signal, entry: st.entry });
  // quoteTimer continues from ARMED — no restart needed
}

function toHunting(reason) {
  clearTimers();
  st.name   = 'HUNTING';
  st.signal = null;
  st.entry  = null;
  broadcast('state_change', { state: 'HUNTING', reason });
  startHunting();
}

function startHunting() {
  st.quoteTimer     = setInterval(pollQuote,      QUOTE_INTERVAL);
  st.proximityTimer = setInterval(
    () => { checkProximity().catch(e => console.error('[PROXIMITY] Error:', e.message)); },
    QUOTE_INTERVAL,
  );

  // Refresh session levels every 30 min
  st.sessionRefreshTimer = setInterval(loadSessionLevels, 30 * 60_000);

  // Refresh at session boundaries (18:00, 22:00, 06:00 PST)
  let lastSessionHour = null;
  st.sessionBoundaryTimer = setInterval(() => {
    const nowHour = parseInt(new Date().toLocaleString('en-US', {
      timeZone: 'America/Los_Angeles', hour: 'numeric', hour12: false,
    }), 10);
    if (lastSessionHour !== null && lastSessionHour !== nowHour && [18, 22, 6].includes(nowHour)) {
      console.log(`Session boundary at hour ${nowHour} PST — refreshing session levels`);
      loadSessionLevels();
    }
    lastSessionHour = nowHour;
  }, 60_000);
}

// ── Routes ────────────────────────────────────────────────────────────────────

app.get('/stream', (req, res) => {
  res.setHeader('Content-Type',  'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection',    'keep-alive');
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.flushHeaders();

  res.write(`event: init\ndata: ${JSON.stringify({
    state:          st.name,
    signal:         st.signal,
    entry:          st.entry,
    lastPrice:      st.lastPrice,
    mode:           st.mode,
    running:        st.running,
    scanLog:        st.scanLog,
    claudeOutput:   st.claudeOutput,
    tvConnected,
    sessionLevels:  st.sessionLevels,
    nearLevel:      st.nearLevel,
    scanStartedAt:  st.scanStartedAt,
    scanFinishedAt: st.scanFinishedAt,
    scanDuration:   st.scanDuration,
    scanActive:     !!st.claudeProc,
    currentSymbol,
  })}\n\n`);

  clients.add(res);

  // If a scan is in progress and this is a reconnecting client, catch them up immediately
  // so they don't see a blank analysis box during the scan
  if (st.claudeProc && st.claudeOutput) {
    res.write(`event: scan_catchup\ndata: ${JSON.stringify({ output: st.claudeOutput, scanCount: st.scanCount })}\n\n`);
  }

  const ka = setInterval(() => {
    try { res.write(': keepalive\n\n'); } catch { clearInterval(ka); clients.delete(res); }
  }, 15_000);

  req.on('close', () => { clearInterval(ka); clients.delete(res); });
});

app.get('/state', (_req, res) => {
  res.json({
    state:         st.name,
    signal:        st.signal,
    entry:         st.entry,
    lastPrice:     st.lastPrice,
    mode:          st.mode,
    running:       st.running,
    scanLog:       st.scanLog,
    sessionLevels: st.sessionLevels,
    nearLevel:     st.nearLevel,
    tvConnected,
    currentSymbol,
  });
});

app.post('/reset', (_req, res) => {
  toHunting('MANUAL_RESET');
  res.json({ ok: true });
});

app.post('/stop', (_req, res) => {
  if (st.claudeProc) { try { st.claudeProc.kill('SIGTERM'); } catch {} }
  clearTimers();
  st.running   = false;
  st.nearLevel = null;
  broadcast('stopped', {});
  broadcast('level_clear', {});
  res.json({ ok: true });
});

app.get('/start', (_req, res) => {
  if (st.running) return res.json({ ok: false, message: 'Already running' });
  st.running       = true;
  st.scannedLevels.clear();
  st.nearLevel     = null;
  st.sessionLevels = null;
  broadcast('started', {});
  res.json({ ok: true });
  toHunting('RESTARTED');
  loadSessionLevels();
});

app.post('/forcescan', (_req, res) => {
  if (!st.running)      return res.json({ ok: false, message: 'Bot is stopped' });
  if (st.name !== 'HUNTING') {
    return res.json({ ok: false, message: `Cannot force scan in ${st.name} state` });
  }
  if (st.claudeProc)    return res.json({ ok: false, message: 'Scan already in progress' });
  if (st.sessionLoading) return res.json({ ok: false, message: 'Session levels still loading' });

  const price = st.lastPrice;
  if (!price) return res.json({ ok: false, message: 'No price available yet' });

  let nearest = null;

  // Try session levels first
  if (st.sessionLevels) {
    const levels = getAllLevels();
    let nearestDist = Infinity;
    for (const level of levels) {
      const dist = Math.abs(price - level.price);
      if (dist < nearestDist) { nearestDist = dist; nearest = level; }
    }
  }

  // Fallback: nearest 50-point round number
  if (!nearest) {
    const below = Math.floor(price / 50) * 50;
    const above = Math.ceil(price  / 50) * 50;
    const roundPrice = (Math.abs(price - below) <= Math.abs(price - above)) ? below : above;
    nearest = { name: `Round ${roundPrice}`, price: roundPrice };
    console.log(`No session levels — scanning nearest round number: ${nearest.price}`);
  }

  // Don't update scannedLevels on force scan — cooldown should only block auto-scans
  runProximityScan(nearest.name, nearest.price);
  res.json({ ok: true, level: nearest.name });
});

app.post('/mode', (req, res) => {
  const { mode } = req.body;
  if (mode !== 'swing' && mode !== 'scalp') {
    return res.status(400).json({ error: 'mode must be swing or scalp' });
  }
  st.mode = mode;
  broadcast('mode_change', { mode });
  res.json({ ok: true, mode });
});

app.post('/symbol', (req, res) => {
  const { symbol } = req.body;
  if (!symbol) return res.status(400).json({ error: 'symbol required' });

  currentSymbol = symbol;
  st.sessionLevels = null;
  st.scannedLevels.clear();
  st.nearLevel = null;
  broadcast('symbol_change', { symbol });
  broadcast('level_clear', {});
  broadcast('session_loading', { message: `Switching to ${symbol}...` });

  // Switch the active TradingView chart to the new symbol, then reload session levels.
  const switchPrompt = `${MCP_PREFIX}Call chart_set_symbol with symbol="${symbol}". No analysis needed.`;
  try {
    writeFileSync(PROMPT_FILE, switchPrompt, 'utf8');
    const child = spawn('cmd', ['/c', 'type dashboard\\current-scan.txt | claude'], {
      stdio: 'ignore',
      cwd:   ROOT,
      shell: true,
    });
    child.on('close', () => loadSessionLevels());
    child.on('error', () => loadSessionLevels());
  } catch (e) {
    console.warn(`chart_set_symbol spawn failed: ${e.message}`);
    loadSessionLevels();
  }

  res.json({ ok: true, symbol });
});

app.post('/cooldown', (req, res) => {
  const { minutes } = req.body;
  if (typeof minutes !== 'number' || minutes < 0) {
    return res.status(400).json({ error: 'minutes must be a non-negative number' });
  }
  SCAN_COOLDOWN = minutes * 60_000;
  console.log(`SCAN_COOLDOWN set to ${minutes} minutes`);
  res.json({ ok: true, cooldown_minutes: minutes, cooldown_ms: SCAN_COOLDOWN });
});

// ── Start ─────────────────────────────────────────────────────────────────────

const PORT = 3001;
app.listen(PORT, () => {
  console.log(`\n  Dashboard → http://localhost:${PORT}\n`);
  initConnection();
  startHunting();
  loadSessionLevels();

  // Heartbeat: every 5s, push nearest level info to all clients
  setInterval(() => {
    const levels = getAllLevels();
    const price  = st.lastPrice;
    let nearest  = null;
    let nearDist = Infinity;
    if (price !== null) {
      for (const lvl of levels) {
        const d = Math.abs(price - lvl.price);
        if (d < nearDist) { nearDist = d; nearest = lvl; }
      }
    }
    broadcast('heartbeat', {
      state:        st.name,
      running:      st.running,
      nearestLevel: nearest ? nearest.name  : null,
      nearestPrice: nearest ? nearest.price : null,
      nearestDist:  nearest ? Math.round(nearDist * 100) / 100 : null,
      scanActive:   !!st.claudeProc,
      timestamp:    Date.now(),
    });
  }, 5_000);
});

process.on('SIGINT', () => {
  console.log('\nShutting down...');
  if (st.claudeProc) { try { st.claudeProc.kill('SIGTERM'); } catch {} }
  clearTimers();
  process.exit(0);
});

process.on('SIGTERM', () => {
  if (st.claudeProc) { try { st.claudeProc.kill('SIGTERM'); } catch {} }
  clearTimers();
  process.exit(0);
});
