"""Futures AI Chart Copilot — local dashboard.

Run:  streamlit run src/futures_copilot/dashboard/app.py
(or `copilot dashboard`)

Read + journal only. No alerts, no broker, no execution buttons — the app
shows what Python computed and lets you journal what YOU decided.
"""

from __future__ import annotations

import json
import time

from futures_copilot.dashboard import data as D
from futures_copilot.dashboard.theme import CSS


def _refresh_symbol(config, symbol: str) -> str:
    """Pull fresh TradingView bars, rebuild features, and run the risk gate."""
    from futures_copilot.data.collector import backfill
    from futures_copilot.data.tvmcp import TradingViewMcpCandleSource
    from futures_copilot.db.store import Store
    from futures_copilot.gate import evaluate
    from futures_copilot.strategies import scan

    source = TradingViewMcpCandleSource(config)
    try:
        with Store(config.db_file) as store:
            store.init_schema()
            report = backfill(config, source, store, [symbol])
            result = scan(store, config, symbol, persist=True)
            gate = evaluate(result.state, result.candidates, store, config, persist=True)
        return f"{report.summary()} | scan: {gate.summary()}"
    finally:
        source.close()


def _collect_symbol(config, symbol: str) -> str:
    """Incremental live pull: newest closed 1m bars, derived TFs, then scan/gate."""
    from futures_copilot.data.collector import collect_once
    from futures_copilot.data.tvmcp import TradingViewMcpCandleSource
    from futures_copilot.db.store import Store
    from futures_copilot.gate import evaluate
    from futures_copilot.strategies import scan

    source = TradingViewMcpCandleSource(config)
    try:
        with Store(config.db_file) as store:
            store.init_schema()
            report = collect_once(config, source, store, [symbol])
            result = scan(store, config, symbol, persist=True)
            gate = evaluate(result.state, result.candidates, store, config, persist=True)
        return f"{report.summary()} | scan: {gate.summary()}"
    finally:
        source.close()


def _quote_symbol(config, symbol: str) -> dict:
    """Fast read of the forming 1m chart price. This is display-only."""
    from futures_copilot.data.tvmcp import TradingViewMcpCandleSource

    source = TradingViewMcpCandleSource(config)
    try:
        q = source.get_quote(symbol)
        price = q.get("header_price") or q.get("last") or q.get("close")
        return {
            "symbol": symbol,
            "price": float(price),
            "quote_time": q.get("time"),
            "synced_at": time.time(),
            "source": "TradingView quote_get",
        }
    finally:
        source.close()


def _autorefresh(seconds: int) -> None:
    """Client-side timer: reruns the dashboard without adding Streamlit plugins."""
    import streamlit.components.v1 as components

    ms = max(1, int(seconds)) * 1000
    components.html(
        f"""
        <script>
        const key = "copilot-live-refresh";
        if (window.parent[key]) {{
          window.parent.clearTimeout(window.parent[key]);
        }}
        window.parent[key] = window.parent.setTimeout(() => {{
          window.parent.location.reload();
        }}, {ms});
        </script>
        """,
        height=0,
        width=0,
    )


def _candles_fig(df, state):
    """Legend-style candle chart (plotly optional — returns None if missing)."""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None
    if df is None or df.empty:
        return None
    import pandas as pd

    idx = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("America/New_York")
    fig = go.Figure(data=[go.Candlestick(
        x=idx, open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        increasing_line_color="#00c805", increasing_fillcolor="#00c805",
        decreasing_line_color="#ff5000", decreasing_fillcolor="#ff5000",
        line=dict(width=1), whiskerwidth=0.6, name="",
    )])
    for name, px, _src in D.levels_ladder(state)[:12]:
        color = "#8b919e" if "VWAP" not in name else "#ffb224"
        fig.add_hline(y=px, line_width=1, line_dash="dot", line_color=color,
                      annotation_text=name, annotation_font_size=10,
                      annotation_font_color=color, annotation_position="right")
    fig.update_layout(
        height=420, margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", color="#8b919e", size=11),
        xaxis=dict(gridcolor="#1a1e27", rangeslider_visible=False),
        yaxis=dict(gridcolor="#1a1e27", side="right"),
        showlegend=False, hovermode="x unified",
    )
    return fig


def main() -> None:  # pragma: no cover - UI wiring, smoke-tested via AppTest
    # Imports live INSIDE main so streamlit's AppTest.from_function can execute
    # it in a fresh module (module globals are not carried over there).
    import json
    import time
    from datetime import date, timedelta
    from html import escape

    import streamlit as st

    from futures_copilot.dashboard.charts import candles_fig as _candles_fig
    from futures_copilot.dashboard import data as D
    from futures_copilot.dashboard.safe_html import h
    from futures_copilot.dashboard.theme import CSS
    from futures_copilot.packet import (
        export_packet_json, packet_from_latest, render_markdown, write_packet,
    )
    from futures_copilot.utils.roll_dates import (
        ROLL_WINDOW_DAYS, get_next_roll_date, is_in_roll_window,
    )

    st.set_page_config(page_title="Futures Copilot", page_icon="◮", layout="wide",
                       initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)

    config = st.session_state.get("_config")
    if config is None:
        config = D.load_config(D.find_config())
        st.session_state["_config"] = config

    # ── sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown('<div class="copilot-brand">◮ Futures Copilot <span class="tick">·</span> '
                    '<span class="subtle">paper</span></div>', unsafe_allow_html=True)
        st.markdown('<div class="subtle">Python calculates · Claude explains · you approve</div>',
                    unsafe_allow_html=True)
        st.write("")
        symbol = st.selectbox("Symbol", list(config.symbols.keys()), index=0, key="sidebar_symbol")
        chart_tf = st.selectbox(
            "Chart timeframe",
            list(config.timeframes.canonical),
            index=list(config.timeframes.canonical).index("1m"),
            key="chart_timeframe",
        )
        live_enabled = st.toggle("Live mode", value=False, key="live_enabled")
        if live_enabled:
            price_interval = st.select_slider(
                "price sync",
                options=[1, 2, 3, 5],
                value=1,
                format_func=lambda s: f"{s}s",
                key="live_price_interval",
            )
            visual_interval = st.select_slider(
                "screen update",
                options=[1, 3, 5, 10],
                value=3,
                format_func=lambda s: f"{s}s",
                key="live_visual_interval",
            )
            scan_interval = st.select_slider(
                "data scan",
                options=[10, 15, 30, 60],
                value=30,
                format_func=lambda s: f"{s}s",
                key="live_scan_interval",
            )
            @st.fragment(run_every=f"{int(visual_interval)}s")
            def live_mode_tick() -> None:
                now = time.time()
                last_quote = float(st.session_state.get("live_last_quote_ts", 0.0))
                last_collect = float(st.session_state.get("live_last_collect_ts", 0.0))
                next_quote_in = max(0, int(price_interval - (now - last_quote)))
                next_collect_in = max(0, int(scan_interval - (now - last_collect)))
                changed = False
                if now - last_quote >= price_interval:
                    try:
                        st.session_state["live_quote"] = _quote_symbol(config, symbol)
                        st.session_state["live_last_quote_ts"] = time.time()
                        st.session_state["live_quote_error"] = None
                        changed = True
                    except Exception as e:
                        hint = getattr(e, "hint", "")
                        st.session_state["live_quote_error"] = (
                            f"{type(e).__name__}: {e}" + (f"\n\nFix: {hint}" if hint else "")
                        )
                        st.session_state["live_last_quote_ts"] = time.time()
                if now - last_collect >= scan_interval:
                    with st.spinner("Live Mode: collecting closed 1m bars and rescanning..."):
                        try:
                            st.session_state["live_status"] = _collect_symbol(config, symbol)
                            st.session_state["live_last_collect_ts"] = time.time()
                            st.session_state["live_error"] = None
                            changed = True
                        except Exception as e:
                            hint = getattr(e, "hint", "")
                            st.session_state["live_error"] = (
                                f"{type(e).__name__}: {e}" + (f"\n\nFix: {hint}" if hint else "")
                            )
                            st.session_state["live_last_collect_ts"] = time.time()
                if changed:
                    st.rerun()

                live_error = st.session_state.get("live_quote_error") or st.session_state.get("live_error")
                live_status = st.session_state.get("live_status", "warming up")
                state_class = "error" if live_error else "ok"
                quote = st.session_state.get("live_quote") or {}
                quote_txt = (
                    f'price {quote.get("price", 0):,.2f} synced {int(max(0, time.time() - quote.get("synced_at", time.time())))}s ago'
                    if quote else "price sync warming up"
                )
                detail = escape(live_error or f"{quote_txt}\n{live_status}")
                st.markdown(
                    f'<div class="live-card {state_class}">'
                    '<div class="live-top"><span class="live-pulse"></span><b>Live Mode</b>'
                    f'<span>price {next_quote_in}s · scan {next_collect_in}s</span></div>'
                    f'<div class="live-detail">{detail}</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )

            live_mode_tick()
        if st.button("Refresh data", use_container_width=True, key="refresh_data"):
            with st.spinner("Pulling TradingView bars and rescanning..."):
                try:
                    st.session_state["refresh_status"] = _refresh_symbol(config, symbol)
                    st.session_state["live_status"] = st.session_state["refresh_status"]
                    st.session_state["live_last_collect_ts"] = time.time()
                    st.session_state.pop("refresh_error", None)
                except Exception as e:  # typed data-source errors include a fix hint
                    hint = getattr(e, "hint", "")
                    st.session_state["refresh_error"] = (
                        f"{type(e).__name__}: {e}" + (f"\n\nFix: {hint}" if hint else "")
                    )
                    st.session_state.pop("refresh_status", None)
            st.rerun()
        if msg := st.session_state.pop("refresh_status", None):
            st.success(msg)
        if err := st.session_state.pop("refresh_error", None):
            st.error(err)
        st.write("")
        st.markdown(
            '<div class="safety"><b>Safety rails.</b> WAIT is the default. Decisions come from '
            'the risk gate only — this dashboard cannot place, size, or alert anything. '
            'Manual paper trading, always.</div>', unsafe_allow_html=True)

    store = D.open_store(config)
    try:
        ov = D.load_overview(store, config, symbol)
        state = ov["state"]
        gv = D.gate_view(ov["latest_signal"])
        live_quote = st.session_state.get("live_quote") or {}
        quote_age = time.time() - float(live_quote.get("synced_at", 0.0))
        quote_is_fresh = live_quote.get("symbol") == symbol and quote_age <= 10
        display_price = float(live_quote["price"]) if state and quote_is_fresh else (
            float(state["current_price"]) if state else None
        )
        live_badge = '<span class="price-live">LIVE</span>' if quote_is_fresh else ""

        # ── header strip ─────────────────────────────────────────────────────
        h1, h2, h3, h4 = st.columns([2.4, 1.6, 1.6, 3.2])
        with h1:
            if state:
                st.markdown(f'<div class="microlabel">{symbol} · {state["trading_day"]}</div>'
                            f'<div class="bigprice">{display_price:,.2f}{live_badge}</div>',
                            unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="microlabel">{symbol}</div>'
                            '<div class="bigprice">—</div>', unsafe_allow_html=True)
        with h2:
            sess = (state or {}).get("session")
            chip = {"asia": "gray", "london": "amber", "ny": "green", None: "gray"}.get(sess, "gray")
            st.markdown('<div class="microlabel">Session</div>'
                        f'<span class="chip {chip}"><span class="dot"></span>{sess or "closed"}</span>',
                        unsafe_allow_html=True)
        with h3:
            fresh, age = D.freshness(ov["state_row"])
            chip = "green" if fresh == "live" else ("amber" if fresh == "stale" else "gray")
            label = "no data" if age is None else (f"{age}m old" if age < 600 else "very stale")
            st.markdown('<div class="microlabel">Data</div>'
                        f'<span class="chip {chip}"><span class="dot"></span>{label}</span>',
                        unsafe_allow_html=True)
        with h4:
            dec = gv["decision"]
            sub = ""
            if gv["candidate"]:
                c = gv["candidate"]
                sub = (f'<span class="sub">{h(c.get("setup_type"))} · grade {h(c.get("grade"))} · '
                       f'RR {c.get("rr", 0):.2f} · {h(D.fmt_ts(c.get("confirmed_close_ts")))}</span>')
            st.markdown(f'<div class="decision {dec}">{dec}{sub}</div>', unsafe_allow_html=True)

        # ── contract roll indicator ──────────────────────────────────────
        # Static CME calendar (utils/roll_dates.py). Display only — this banner
        # never gates anything in code; it reminds the human. Anchored to the
        # trading day on screen, falling back to today before the first scan.
        roll_day = date.fromisoformat(state["trading_day"]) if state else date.today()
        next_roll = get_next_roll_date(roll_day)
        window_opens = (next_roll - timedelta(days=ROLL_WINDOW_DAYS)) if next_roll else None
        if is_in_roll_window(roll_day):
            st.error("🚨 ROLL WINDOW ACTIVE — No trades allowed.")
        elif window_opens is not None and (window_opens - roll_day) <= timedelta(hours=48):
            st.warning("⚠️ Roll window approaching within 48 hours. "
                       "Monitor contract volume shift.")
        else:
            st.caption("✅ Normal contract cycle trading.")

        if not state:
            st.info("No market state yet. Run `copilot backfill` then `copilot scan --symbol "
                    f"{symbol}` and refresh.")
            return

        # ── Desk Reminders: preflight journal memory. Reads ONLY the local
        # cache (+ two cheap MAX(id) staleness lookups). Journaling in the
        # forms below flips this to 'stale'; the refresh button rebuilds the
        # LOCAL cache and nothing else. ─────────────────────────────────────
        st.write("")
        pf = D.preflight_view(store, config, symbol, state)
        with st.container(border=True):
            r1, r2 = st.columns([2.0, 3.8], gap="small")
            with r1:
                st.markdown('<div class="microlabel">Desk Reminders · journal memory</div>',
                            unsafe_allow_html=True)
                pf_chip, pf_txt = {
                    "cached": ("green", "cached"),
                    "stale": ("amber", "stale — journal changed"),
                    "missing": ("gray", "missing"),
                    "disabled": ("gray", "disabled"),
                }.get(pf["status"], ("gray", pf["status"]))
                st.markdown(f'<div class="stat"><span class="k">preflight cache</span>'
                            f'<span class="v"><span class="chip {pf_chip}"><span class="dot"></span>'
                            f'{pf_txt}</span></span></div>', unsafe_allow_html=True)
                mem = pf["cache"]
                if mem:
                    perf = mem.get("performance") or {}
                    avg = perf.get("avg_result_r")
                    st.markdown(
                        f'<div class="stat"><span class="k">last {perf.get("total_reviewed", 0)} reviews</span>'
                        f'<span class="v">{perf.get("wins", 0)}W / {perf.get("losses", 0)}L / '
                        f'{perf.get("scratches", 0)} scratch · {perf.get("skipped", 0)} skipped'
                        f'{f" · avg {avg:+.2f}R" if avg is not None else ""}</span></div>',
                        unsafe_allow_html=True)
                    gov_m = mem.get("governor") or {}
                    if gov_m:
                        tone = "green" if gov_m.get("clear") else "red"
                        st.markdown(f'<div class="stat"><span class="k">governor at build</span>'
                                    f'<span class="v {tone}">'
                                    f'{"clear" if gov_m.get("clear") else "done for the day"} · '
                                    f'{gov_m.get("wins", 0)}W / {gov_m.get("losses", 0)}L</span></div>',
                                    unsafe_allow_html=True)
                    for t in (mem.get("top_mistake_tags") or [])[:5]:
                        st.markdown(f'<div class="stat"><span class="k">mistake</span>'
                                    f'<span class="v"><b>{h(t.get("tag"))}</b> ×{h(t.get("count"))}'
                                    f'</span></div>', unsafe_allow_html=True)
            with r2:
                st.markdown('<div class="microlabel">Do-not-repeat reminders</div>',
                            unsafe_allow_html=True)
                mem = pf["cache"]
                if mem:
                    bullets = (mem.get("warnings") or [])[:5]
                    if bullets:
                        for w in bullets:
                            st.markdown(f'<div class="stat"><span class="k">! {h(w)}</span></div>',
                                        unsafe_allow_html=True)
                    else:
                        st.markdown('<div class="stat"><span class="k">no repeated-mistake patterns '
                                    'in the lookback window</span><span class="v muted">keep journaling'
                                    '</span></div>', unsafe_allow_html=True)
                    for rl in (mem.get("mistake_rules") or [])[:3]:
                        st.markdown(f'<div class="stat"><span class="k">rule [{h(rl.get("tag"))}]</span>'
                                    f'<span class="v">{h(rl.get("rule"))}</span></div>',
                                    unsafe_allow_html=True)
                elif pf["status"] == "missing":
                    st.markdown('<div class="stat"><span class="k">no desk-memory cache for '
                                f'{state["trading_day"]}</span><span class="v muted">run '
                                '<code>copilot preflight</code> before the session</span></div>',
                                unsafe_allow_html=True)
                if pf["status"] in ("missing", "stale"):
                    if st.button("Refresh preflight memory", key="refresh_preflight",
                                 help="Rebuilds the local journal-memory cache from SQLite. "
                                      "Touches nothing else — no TradingView, no Obsidian, no Claude."):
                        D.rebuild_preflight(store, config, symbol, state["trading_day"])
                        st.rerun()

        # ── Desk Mode strip: plan + gates + ops. Everything here comes from the
        # closed-candle scan, the risk gate, and cheap local reads. It never
        # waits on Claude/Fable or the Obsidian vault. ──────────────────────
        st.write("")
        desk = D.desk_mode_status(store, config, state)
        d1, d2, d3 = st.columns([2.2, 1.9, 1.7], gap="small")
        with d1:
            with st.container(border=True):
                st.markdown('<div class="microlabel">Desk Mode · trade plan (manual paper only)</div>',
                            unsafe_allow_html=True)
                cand = gv["candidate"]
                if cand:
                    st.markdown(
                        f'<div class="stat"><span class="k">entry zone</span><span class="v">'
                        f'{h(cand.get("entry_lo"))} – {h(cand.get("entry_hi"))} (ref {h(cand.get("entry_ref"))})'
                        f'</span></div>'
                        f'<div class="stat"><span class="k">stop</span><span class="v">{h(cand.get("stop"))}</span></div>'
                        f'<div class="stat"><span class="k">target</span><span class="v">'
                        f'{h(cand.get("target"))} ({h(cand.get("target_name"))}) · RR {cand.get("rr", 0):.2f}</span></div>',
                        unsafe_allow_html=True)
                else:
                    st.markdown('<div class="stat"><span class="k">no current candidate</span>'
                                '<span class="v muted">WAIT is the default</span></div>',
                                unsafe_allow_html=True)
                for inv in (gv["invalidation"] or [])[:3]:
                    st.markdown(f'<div class="stat"><span class="k">✕ {h(inv)}</span></div>',
                                unsafe_allow_html=True)
                for rr_ in (gv["reasons"] or [])[:4]:
                    st.markdown(f'<div class="stat"><span class="k">reject</span>'
                                f'<span class="v red">{h(rr_)}</span></div>', unsafe_allow_html=True)
        with d2:
            with st.container(border=True):
                st.markdown('<div class="microlabel">Desk Mode · gates</div>', unsafe_allow_html=True)
                gh = desk.get("golden_hour") or {}
                if not gh:
                    gh_chip, gh_txt = "gray", "unknown"
                elif not gh.get("enforced"):
                    gh_chip, gh_txt = "gray", "off"
                elif gh.get("within"):
                    gh_chip, gh_txt = "green", f'inside {gh["window"][0]}–{gh["window"][1]}'
                else:
                    gh_chip, gh_txt = "red", f'outside {gh["window"][0]}–{gh["window"][1]}'
                st.markdown(f'<div class="stat"><span class="k">golden hour</span>'
                            f'<span class="v"><span class="chip {gh_chip}"><span class="dot"></span>'
                            f'{gh_txt}</span></span></div>', unsafe_allow_html=True)
                gov = desk.get("governor") or {}
                gov_chip = "green" if gov.get("clear") else "red"
                gov_txt = ("clear" if gov.get("clear") else "done for the day")
                st.markdown(f'<div class="stat"><span class="k">trade governor</span>'
                            f'<span class="v"><span class="chip {gov_chip}"><span class="dot"></span>'
                            f'{gov_txt}</span> {gov.get("wins", 0)}W / {gov.get("losses", 0)}L</span></div>',
                            unsafe_allow_html=True)
                eq = D.checklist_item(gv, "stop_not_at_equal_liquidity")
                if eq is None:
                    eq_chip, eq_txt = "gray", "not gated yet"
                elif eq.get("passed"):
                    eq_chip, eq_txt = "green", "stop clear"
                else:
                    eq_chip, eq_txt = "red", "stop at equal highs/lows"
                st.markdown(f'<div class="stat"><span class="k">equal-level stop</span>'
                            f'<span class="v"><span class="chip {eq_chip}"><span class="dot"></span>'
                            f'{eq_txt}</span></span></div>', unsafe_allow_html=True)
        with d3:
            with st.container(border=True):
                st.markdown('<div class="microlabel">Desk Mode · ops</div>', unsafe_allow_html=True)
                pkt_ready = gv["candidate"] is not None or gv["decision"] != "WAIT"
                st.markdown(f'<div class="stat"><span class="k">packet</span>'
                            f'<span class="v">{"signal packet ready" if pkt_ready else "WAIT packet"}'
                            f'</span></div>', unsafe_allow_html=True)
                prep = desk.get("prep") or {}
                st.markdown(f'<div class="stat"><span class="k">vault prep cache</span>'
                            f'<span class="v">{"cached" if prep.get("exists") else "none — optional, run `copilot prep`"}'
                            f'</span></div>', unsafe_allow_html=True)
                fresh2, age2 = D.freshness(ov["state_row"])
                st.markdown(f'<div class="stat"><span class="k">closed-candle scan</span>'
                            f'<span class="v">{fresh2}{f" · {age2}m old" if age2 is not None else ""}'
                            f'</span></div>', unsafe_allow_html=True)
                q_txt = (f'{int(quote_age)}s old' if quote_is_fresh
                         else ("stale" if live_quote else "off (enable Live mode)"))
                st.markdown(f'<div class="stat"><span class="k">live quote sync</span>'
                            f'<span class="v">{q_txt}</span></div>', unsafe_allow_html=True)

        st.write("")
        tabs = st.tabs(["Overview", "Signals", "Journal", "Mistakes", "Memory", "Packet"])

        # ── overview ─────────────────────────────────────────────────────────
        with tabs[0]:
            left, right = st.columns([2.5, 1.25], gap="small")
            with left:
                with st.container(border=True):
                    st.markdown(f'<div class="microlabel">Price · {chart_tf}</div>', unsafe_allow_html=True)
                    df5 = store.get_candles_df(symbol, chart_tf, limit=240 if chart_tf == "1m" else 180)
                    fig = _candles_fig(df5, state)
                    if fig is not None:
                        st.plotly_chart(fig, use_container_width=True,
                                        config={"displayModeBar": False})
                    elif not df5.empty:
                        st.line_chart(df5.set_index(df5.index)["close"], height=380)
                        st.caption("Install plotly for Legend-style candles: pip install plotly")
                    else:
                        st.caption("no 5m bars stored yet")
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Latest candidates (pre-gate)</div>',
                                unsafe_allow_html=True)
                    if ov["candidates"]:
                        for c in ov["candidates"][:5]:
                            cj = json.loads(c["json_output"])
                            side = "green" if c["direction"] == "long" else "red"
                            st.markdown(
                                f'<div class="stat"><span class="k">'
                                f'<span class="chip {side}"><span class="dot"></span>{h(c["direction"])}</span>'
                                f'&nbsp; {h(c["strategy_name"])} · grade {h(c["grade"])} · swept '
                                f'{h((cj.get("context") or {}).get("swept_level", "—"))}</span>'
                                f'<span class="v">entry {h(cj.get("entry_ref"))} · stop {h(cj.get("stop"))} '
                                f'· tgt {h(cj.get("target"))} · RR {cj.get("rr", 0):.2f}</span></div>',
                                unsafe_allow_html=True)
                    else:
                        st.markdown('<div class="stat"><span class="k">no candidates in the last scans'
                                    '</span><span class="v muted">WAIT</span></div>',
                                    unsafe_allow_html=True)
            with right:
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Level ladder</div>', unsafe_allow_html=True)
                    px = display_price if display_price is not None else state["current_price"]
                    rows = D.levels_ladder(state)
                    shown_px = False
                    for name, lvl, src in rows:
                        if not shown_px and lvl <= px:
                            st.markdown(f'<div class="lvl"><span class="name" style="color:#46e264">'
                                        f'▸ price</span><span class="px" style="color:#46e264">{px:,.2f}'
                                        f'</span><span class="src">now</span></div>', unsafe_allow_html=True)
                            shown_px = True
                        st.markdown(f'<div class="lvl"><span class="name">{h(name)}</span>'
                                    f'<span class="px">{lvl:,.2f}</span>'
                                    f'<span class="src">{h(src)}</span></div>', unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Context</div>', unsafe_allow_html=True)
                    vpos = state.get("vwap_position")
                    pdd = state.get("premium_discount_day")
                    drp = state.get("day_range_position")
                    rows = [
                        ("VWAP side", vpos or "—", "green" if vpos == "above" else "red"),
                        ("Day range", pdd or "—", "green" if pdd == "discount" else ("red" if pdd == "premium" else "")),
                        ("Range position", f"{drp:.1%}" if drp is not None else "—", ""),
                        ("ATR 5m", f'{state.get("atr_5m"):.2f}' if state.get("atr_5m") else "—", ""),
                        ("ATR 15m", f'{state.get("atr_15m"):.2f}' if state.get("atr_15m") else "—", ""),
                        ("Daily bias", (ov["bias"] or {}).get("bias", "not set"), ""),
                    ]
                    for k, v, tone in rows:
                        st.markdown(f'<div class="stat"><span class="k">{k}</span>'
                                    f'<span class="v {tone}">{h(v)}</span></div>', unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Bar coverage</div>', unsafe_allow_html=True)
                    for c in ov["coverage"]:
                        st.markdown(f'<div class="stat"><span class="k">{h(c["timeframe"])}</span>'
                                    f'<span class="v">{h(c["bars"])} bars · to {h(c["last"])}</span></div>',
                                    unsafe_allow_html=True)

        # ── signals ──────────────────────────────────────────────────────────
        with tabs[1]:
            colA, colB = st.columns([1.4, 2.2], gap="small")
            with colA:
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Signal history</div>', unsafe_allow_html=True)
                    if not ov["signals"]:
                        st.caption("no gated signals yet — `copilot scan` writes them")
                    ids = []
                    for srow in ov["signals"]:
                        ids.append(srow["id"])
                        tone = {"LONG": "green", "SHORT": "red", "REJECT": "gray"}.get(srow["decision"], "amber")
                        st.markdown(
                            f'<div class="stat"><span class="k">#{h(srow["id"])} · {h(srow["trading_day"])} '
                            f'{h(srow["session"])} · {h(srow["setup"])}</span>'
                            f'<span class="v"><span class="chip {tone}"><span class="dot"></span>'
                            f'{h(srow["decision"])}</span></span></div>', unsafe_allow_html=True)
            with colB:
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Inspect signal</div>', unsafe_allow_html=True)
                    if ov["signals"]:
                        pick = st.selectbox("signal", [s["id"] for s in ov["signals"]],
                                            format_func=lambda i: f"#{i}", label_visibility="collapsed", key="signal_pick")
                        srow = next(s for s in ov["signals"] if s["id"] == pick)
                        g = D.gate_view(srow)
                        cand = g["candidate"] or {}
                        if cand:
                            st.markdown(
                                f'<div class="stat"><span class="k">geometry</span><span class="v">'
                                f'entry {h(cand.get("entry_lo"))}–{h(cand.get("entry_hi"))} · stop {h(cand.get("stop"))} '
                                f'· target {h(cand.get("target"))} ({h(cand.get("target_name"))}) · '
                                f'RR {cand.get("rr", 0):.2f} · grade {h(cand.get("grade"))}</span></div>',
                                unsafe_allow_html=True)
                            st.markdown(f'<div class="stat"><span class="k">confluences</span>'
                                        f'<span class="v">{h(", ".join(cand.get("confluences", []) or ["—"]))}'
                                        f'</span></div>', unsafe_allow_html=True)
                        for chk in g["checklist"]:
                            cls = "pass" if chk.get("passed") else "fail"
                            st.markdown(f'<div class="chk {cls}"><span class="mark">'
                                        f'{"PASS" if chk.get("passed") else "FAIL"}</span>'
                                        f'<span>{h(chk.get("check"))}</span>'
                                        f'<span class="why">{h(chk.get("detail"))}</span></div>',
                                        unsafe_allow_html=True)
                        if g["invalidation"]:
                            st.markdown('<div class="microlabel" style="margin-top:.6rem">Invalidation</div>',
                                        unsafe_allow_html=True)
                            for inv in g["invalidation"]:
                                st.markdown(f'<div class="stat"><span class="k">✕ {h(inv)}</span></div>',
                                            unsafe_allow_html=True)
                    else:
                        st.caption("nothing to inspect yet")

        # ── journal ──────────────────────────────────────────────────────────
        with tabs[2]:
            j1, j2 = st.columns(2, gap="small")
            with j1:
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Daily bias</div>', unsafe_allow_html=True)
                    with st.form("bias_form", clear_on_submit=False):
                        b_day = st.text_input("trading day", value=state["trading_day"])
                        b_bias = st.selectbox("bias", ["bullish", "bearish", "neutral", "two_sided"], key="bias_select")
                        b_notes = st.text_area("thesis / notes", height=90)
                        if st.form_submit_button("Save bias"):
                            store.set_daily_bias(b_day, symbol, b_bias, b_notes)
                            st.success(f"bias saved for {b_day}")
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Log a mistake</div>', unsafe_allow_html=True)
                    with st.form("mistake_form", clear_on_submit=True):
                        m_tag = st.text_input("tag (e.g. chased_entry)")
                        m_desc = st.text_area("what happened", height=70)
                        m_rule = st.text_input("rule update that prevents a repeat")
                        if st.form_submit_button("Log mistake") and m_tag:
                            store.add_mistake(m_tag, m_desc, m_rule, symbol)
                            st.success(f"logged: {m_tag}")
            with j2:
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Journal a signal result</div>',
                                unsafe_allow_html=True)
                    with st.form("result_form", clear_on_submit=True):
                        sig_ids = [s["id"] for s in ov["signals"]] or [0]
                        r_sig = st.selectbox("signal id", sig_ids, format_func=lambda i: f"#{i}", key="result_signal_id")
                        r_taken = st.toggle("I (paper) took this trade", value=True, key="result_taken")
                        r_r = st.number_input("result in R", value=0.0, step=0.1, format="%.2f")
                        r_tags = st.multiselect("mistake tags",
                                                [m["tag"] for m in ov["mistakes"]] or
                                                ["chased_entry", "exited_early", "moved_stop", "revenge_trade"],
                                                key="result_mistake_tags")
                        r_notes = st.text_area("review notes", height=90)
                        if st.form_submit_button("Save review"):
                            if store.get_signal(int(r_sig)) is None:
                                st.error(f"signal #{r_sig} does not exist")
                            else:
                                store.add_trade_review(
                                    signal_id=int(r_sig), symbol=symbol, taken=bool(r_taken),
                                    result_r=float(r_r) if r_taken else None,
                                    mistake_tags=json.dumps(r_tags), notes=r_notes)
                                st.success(f"review saved for #{r_sig}")
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Recent reviews</div>', unsafe_allow_html=True)
                    if ov["reviews"]:
                        for r in ov["reviews"][:8]:
                            res = f'{r["result_r"]:+.2f}R' if r["result_r"] is not None else ("taken" if r["taken"] else "skipped")
                            tone = "green" if (r["result_r"] or 0) > 0 else ("red" if (r["result_r"] or 0) < 0 else "muted")
                            st.markdown(f'<div class="stat"><span class="k">#{h(r["signal_id"])} · '
                                        f'{h((r["notes"] or "")[:60])}</span>'
                                        f'<span class="v {tone}">{h(res)}</span></div>', unsafe_allow_html=True)
                    else:
                        st.caption("no reviews yet")

        # ── mistakes ─────────────────────────────────────────────────────────
        with tabs[3]:
            with st.container(border=True):
                st.markdown('<div class="microlabel">Mistake ledger — what not to repeat</div>',
                            unsafe_allow_html=True)
                if ov["mistakes"]:
                    for m in ov["mistakes"]:
                        st.markdown(f'<div class="stat"><span class="k"><b>{h(m["tag"])}</b> — '
                                    f'{h(m["description"] or "")}</span>'
                                    f'<span class="v muted">{h(m["rule_update"] or "")}</span></div>',
                                    unsafe_allow_html=True)
                else:
                    st.caption("clean slate — log mistakes from the Journal tab")

        # ── memory ───────────────────────────────────────────────────────────
        with tabs[4]:
            with st.container(border=True):
                st.markdown('<div class="microlabel">Similar past setups (deterministic SQL memory)</div>',
                            unsafe_allow_html=True)
                if ov["signals"]:
                    pick2 = st.selectbox("compare signal", [s["id"] for s in ov["signals"]],
                                         format_func=lambda i: f"#{i}", key="mem_pick",
                                         label_visibility="collapsed")
                    srow = next(s for s in ov["signals"] if s["id"] == pick2)
                    sims = D.similar_for_signal(store, srow)
                    if sims:
                        for s_ in sims:
                            oc = s_.get("outcome") or {}
                            oc_txt = (f'{oc.get("result_r"):+.2f}R' if oc.get("result_r") is not None
                                      else ("taken" if oc.get("taken") else "no review"))
                            tone = "green" if (oc.get("result_r") or 0) > 0 else ("red" if (oc.get("result_r") or 0) < 0 else "muted")
                            st.markdown(f'<div class="stat"><span class="k">#{h(s_["signal_id"])} · '
                                        f'{h(s_["trading_day"])} {h(s_["session"])} · {h(s_["decision"])} · '
                                        f'grade {h(s_["grade"])} · RR {h(s_["rr"])} · swept {h(s_["swept_level"])}</span>'
                                        f'<span class="v {tone}">{h(oc_txt)}</span></div>',
                                        unsafe_allow_html=True)
                    else:
                        st.caption("no similar setups on record yet — memory grows as you journal")
                else:
                    st.caption("no signals yet")

        # ── packet ───────────────────────────────────────────────────────────
        with tabs[5]:
            # Build (or reuse) the packet OUTSIDE the render fragment. The DB
            # and prep/preflight filesystem reads run at most once per new
            # signal/state/journal edit, and the heavy text blocks render
            # inside an isolated fragment — live-mode refresh ticks and the
            # rest of the dashboard never wait on them.
            sig_row = ov["latest_signal"]
            pkt_key = (f'{symbol}:{sig_row["id"] if sig_row else "none"}:{state.get("ts")}:'
                       f'{len(ov["mistakes"])}:{len(ov["reviews"])}:'
                       f'{(ov["bias"] or {}).get("bias")}:{pf["status"]}')
            pc = st.session_state.get("_packet_cache") or {}
            if pc.get("key") != pkt_key:
                try:
                    packet = packet_from_latest(store, config, symbol)
                    pc = {"key": pkt_key, "packet": packet,
                          "md": render_markdown(packet),
                          "json": export_packet_json(packet), "error": None}
                except ValueError as e:
                    pc = {"key": pkt_key, "packet": None, "md": "", "json": "",
                          "error": str(e)}
                st.session_state["_packet_cache"] = pc

            @st.fragment
            def packet_tab() -> None:
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Claude prompt packet</div>',
                                unsafe_allow_html=True)
                    st.caption("Claude explains, grades, warns, journals. The decision field does "
                               "not exist in its output schema — the gate already decided.")
                    if pc["error"]:
                        st.info(pc["error"])
                        return
                    pcol1, pcol2 = st.columns([1, 1])
                    with pcol1:
                        if st.button("Write packet files (JSON + MD)"):
                            jp, mp = write_packet(pc["packet"], config)
                            st.success(f"written: {jp.name}, {mp.name} → {jp.parent}")
                        st.download_button("Download JSON", pc["json"],
                                           file_name="copilot_packet.json", mime="application/json")
                    with pcol2:
                        st.download_button("Download Markdown", pc["md"],
                                           file_name="copilot_packet.md", mime="text/markdown")
                    view = st.radio("view", ["markdown", "json"], horizontal=True,
                                    label_visibility="collapsed", key="packet_view")
                    if view == "markdown":
                        st.markdown(pc["md"])
                    else:
                        st.json(pc["packet"], expanded=False)

            packet_tab()
    finally:
        store.close()


def _under_streamlit() -> bool:
    try:
        from streamlit.runtime import exists
        return exists()
    except Exception:
        return False


if _under_streamlit():  # pragma: no cover - exercised by `streamlit run`
    main()
