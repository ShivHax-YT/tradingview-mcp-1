"""Desk Memory / Preflight v0.3: deterministic journal memory, cache
lifecycle, packet embedding, CLI, dashboard status — and proof that the live
scan/gate path never needs any of it."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from futures_copilot.config import PreflightConfig
from futures_copilot.gate import evaluate
from futures_copilot.packet import packet_from_latest, render_markdown
from futures_copilot.preflight import (
    build_preflight, load_preflight, preflight_cache_path, preflight_is_stale,
    write_preflight,
)
from futures_copilot.strategies import scan

from .strategy_fixtures import seed_long_trap

ROOT = Path(__file__).parent.parent
DAY = "2026-06-24"


@pytest.fixture()
def pf_config(config, tmp_path):
    config.preflight.memory_dir = str(tmp_path / "session_memory")
    return config


def _sig(store, *, day=DAY, direction="long", session="ny", grade="A",
         setup="session_liquidity_trap", rr=2.0, n=0):
    js = json.dumps({"candidate": {"direction": direction, "setup_type": setup}})
    return store.save_signal(
        symbol="MNQ", ts=1_782_000_000 + n * 300, session=session, trading_day=day,
        decision="LONG", setup=setup, grade=grade, entry_lo=1, entry_hi=2, stop=0,
        tp1=5, tp2=None, rr=rr, reasons="[]", warnings="[]", invalidation="[]",
        json_signal=js)


def _review(store, sid, *, taken=True, result_r=None, tags="[]"):
    return store.add_trade_review(signal_id=sid, symbol="MNQ", taken=taken,
                                  result_r=result_r, mistake_tags=tags)


# ── config ───────────────────────────────────────────────────────────────────

def test_preflight_config_defaults():
    p = PreflightConfig()
    assert p.enabled is True
    assert p.memory_dir == "data/session_memory"
    assert p.lookback_reviews == 50 and p.lookback_days == 20
    assert p.top_mistakes == 5 and p.min_samples_for_pattern == 3


def test_preflight_config_loaded_from_yaml(config):
    assert config.preflight.enabled is True
    assert config.preflight.memory_dir == "data/session_memory"


# ── build: empty and mixed journals ─────────────────────────────────────────

def test_build_with_empty_journal(pf_config, store):
    mem = build_preflight(store, pf_config, "MNQ", DAY)
    assert mem["preflight_schema"] == "copilot.preflight.v1"
    assert mem["symbol"] == "MNQ" and mem["trading_day"] == DAY
    perf = mem["performance"]
    assert perf["total_reviewed"] == 0 and perf["taken"] == 0 and perf["skipped"] == 0
    assert perf["wins"] == perf["losses"] == perf["scratches"] == 0
    assert perf["avg_result_r"] is None
    assert mem["top_mistake_tags"] == [] and mem["setup_performance"] == []
    assert any("journal" in w for w in mem["warnings"])   # nudge to journal
    path = write_preflight(mem, pf_config)
    assert path.exists() and load_preflight(pf_config, "MNQ", DAY) is not None


def test_build_counts_wins_losses_skips_scratch(pf_config, store):
    _review(store, _sig(store, n=1), taken=True, result_r=2.0)     # win
    _review(store, _sig(store, n=2), taken=True, result_r=-1.0)    # loss
    _review(store, _sig(store, n=3), taken=True, result_r=0.0)     # scratch
    _review(store, _sig(store, n=4), taken=False)                  # skipped
    _review(store, _sig(store, n=5), taken=True, result_r=None)    # unresolved

    perf = build_preflight(store, pf_config, "MNQ", DAY)["performance"]
    assert perf["total_reviewed"] == 5
    assert perf["taken"] == 4 and perf["skipped"] == 1
    assert perf["wins"] == 1 and perf["losses"] == 1 and perf["scratches"] == 1
    assert perf["avg_result_r"] == round((2.0 - 1.0 + 0.0) / 3, 2)  # taken WITH result


def test_top_mistake_tags_aggregate_from_json(pf_config, store):
    _review(store, _sig(store, n=1), result_r=-1.0, tags='["chased_entry", "moved_stop"]')
    _review(store, _sig(store, n=2), result_r=-0.5, tags='["chased_entry"]')
    _review(store, _sig(store, n=3), result_r=1.0, tags='["chased_entry"]')
    _review(store, _sig(store, n=4), result_r=1.0, tags="not-json")   # tolerated
    _review(store, _sig(store, n=5), result_r=1.0, tags=None)         # tolerated

    mem = build_preflight(store, pf_config, "MNQ", DAY)
    tags = {t["tag"]: t["count"] for t in mem["top_mistake_tags"]}
    assert tags["chased_entry"] == 3 and tags["moved_stop"] == 1
    # 3 repeats >= min_samples_for_pattern -> surfaced as a warning bullet
    assert any("chased_entry" in w and "3x" in w for w in mem["warnings"])


def test_top_mistakes_respects_cap(pf_config, store):
    pf_config.preflight.top_mistakes = 2
    for i in range(4):
        _review(store, _sig(store, n=i), result_r=-0.5, tags=f'["tag_{i}"]')
    mem = build_preflight(store, pf_config, "MNQ", DAY)
    assert len(mem["top_mistake_tags"]) == 2


def test_setup_performance_grouping_and_min_samples(pf_config, store):
    # 3 reviewed long/ny trap trades -> a pattern (min_samples default 3)
    _review(store, _sig(store, n=1, direction="long"), result_r=-1.0)
    _review(store, _sig(store, n=2, direction="long"), result_r=-0.5)
    _review(store, _sig(store, n=3, direction="long"), result_r=1.5)
    # only 2 shorts -> noise, no pattern
    _review(store, _sig(store, n=4, direction="short"), result_r=1.0)
    _review(store, _sig(store, n=5, direction="short"), result_r=1.0)

    mem = build_preflight(store, pf_config, "MNQ", DAY)
    groups = {(g["setup"], g["direction"], g["session"]): g
              for g in mem["setup_performance"]}
    key = ("session_liquidity_trap", "long", "ny")
    assert key in groups
    g = groups[key]
    assert g["samples"] == 3 and g["wins"] == 1 and g["losses"] == 2
    assert g["avg_result_r"] == 0.0                       # (-1 - 0.5 + 1.5) / 3
    assert ("session_liquidity_trap", "short", "ny") not in groups


def test_negative_pattern_becomes_warning(pf_config, store):
    for i, r in enumerate((-1.0, -1.0, -0.5)):
        _review(store, _sig(store, n=i, direction="long"), result_r=r)
    mem = build_preflight(store, pf_config, "MNQ", DAY)
    assert any("session_liquidity_trap long in ny" in w and "respect the stats" in w
               for w in mem["warnings"])


def test_lookback_days_excludes_old_signals(pf_config, store):
    _review(store, _sig(store, n=1, day="2026-05-01"), result_r=5.0)   # ancient
    _review(store, _sig(store, n=2, day=DAY), result_r=-1.0)
    perf = build_preflight(store, pf_config, "MNQ", DAY)["performance"]
    assert perf["total_reviewed"] == 1 and perf["losses"] == 1 and perf["wins"] == 0


def test_mistake_rules_from_ledger(pf_config, store):
    store.add_mistake("chased_entry", "entered 30pts late", "wait for the retest")
    store.add_mistake("no_rule_tag", "meh", "")           # empty rule -> excluded
    mem = build_preflight(store, pf_config, "MNQ", DAY)
    rules = {r["tag"]: r["rule"] for r in mem["mistake_rules"]}
    assert rules == {"chased_entry": "wait for the retest"}
    assert any("do-not-repeat [chased_entry]" in b for b in mem["packet_bullets"])


# ── cache lifecycle ──────────────────────────────────────────────────────────

def test_load_missing_returns_none(pf_config):
    assert load_preflight(pf_config, "MNQ", DAY) is None
    assert load_preflight(pf_config, "MNQ", None) is None


def test_load_disabled_returns_none(pf_config, store):
    write_preflight(build_preflight(store, pf_config, "MNQ", DAY), pf_config)
    pf_config.preflight.enabled = False
    assert load_preflight(pf_config, "MNQ", DAY) is None


def test_cache_path_shape(pf_config):
    p = preflight_cache_path(pf_config, "MNQ", DAY)
    assert p.name == f"MNQ_{DAY}.json"


def test_staleness_flips_when_journal_grows(pf_config, store):
    _review(store, _sig(store, n=1), result_r=1.0)
    mem = build_preflight(store, pf_config, "MNQ", DAY)
    assert preflight_is_stale(store, mem) is False
    _review(store, _sig(store, n=2), result_r=-1.0)       # journal moved on
    assert preflight_is_stale(store, mem) is True
    store2_mem = build_preflight(store, pf_config, "MNQ", DAY)
    assert preflight_is_stale(store, store2_mem) is False
    store.add_mistake("fomo", "chased the open", "wait for 9:30 structure")
    assert preflight_is_stale(store, store2_mem) is True  # mistakes count too


# ── packet integration ───────────────────────────────────────────────────────

FORBIDDEN_OUTPUT_FIELDS = ("decision", "action", "entry", "stop", "target", "size",
                           "execute", "order", "approve")


def test_packet_embeds_preflight_memory_schema_unchanged(pf_config, store):
    seed_long_trap(store)
    result = scan(store, pf_config, "MNQ", persist=True)
    gate = evaluate(result.state, result.candidates, store, pf_config, persist=True)
    assert gate.decision == "LONG"
    _review(store, gate.chosen.signal_id, taken=True, result_r=1.2,
            tags='["exited_early"]')
    write_preflight(build_preflight(store, pf_config, "MNQ", DAY), pf_config)

    packet = packet_from_latest(store, pf_config, "MNQ")
    mem = packet["preflight_memory"]
    assert mem is not None and mem["performance"]["total_reviewed"] == 1
    schema = packet["claude_output_schema"]
    assert schema["additionalProperties"] is False
    for field in FORBIDDEN_OUTPUT_FIELDS:
        assert field not in schema["properties"]
    md = render_markdown(packet)
    assert "Desk Memory / Preflight" in md


def test_packet_works_without_preflight_cache(pf_config, store):
    seed_long_trap(store)
    result = scan(store, pf_config, "MNQ", persist=True)
    evaluate(result.state, result.candidates, store, pf_config, persist=True)
    packet = packet_from_latest(store, pf_config, "MNQ")
    assert packet["preflight_memory"] is None
    assert "run `copilot preflight`" in render_markdown(packet)


# ── CLI ──────────────────────────────────────────────────────────────────────

MINI_CONFIG = """\
symbols:
  MNQ: {chart_symbol: "CME_MINI:MNQ1!", tick_size: 0.25, tick_value: 0.50}
timeframes:
  canonical: ["1m"]
  tradingview_map: {1m: "1"}
app:
  db_path: data/test.db
preflight:
  memory_dir: data/session_memory
"""


def test_cli_preflight_end_to_end(tmp_path, capsys):
    from futures_copilot.cli import main as cli_main

    cfg = tmp_path / "config.yaml"
    cfg.write_text(MINI_CONFIG, encoding="utf-8")
    rc = cli_main(["--config", str(cfg), "preflight", "--symbol", "MNQ", "--day", DAY])
    assert rc == 0
    out = capsys.readouterr().out
    assert "desk memory cached" in out and "reviews counted: 0" in out
    assert (tmp_path / "data" / "session_memory" / f"MNQ_{DAY}.json").exists()


# ── dashboard helper ─────────────────────────────────────────────────────────

def test_dashboard_preflight_view_status_cycle(pf_config, store):
    pytest.importorskip("streamlit")
    from futures_copilot.dashboard import data as D

    state = {"trading_day": DAY, "symbol": "MNQ"}
    assert D.preflight_view(store, pf_config, "MNQ", state)["status"] == "missing"
    assert D.preflight_view(store, pf_config, "MNQ", None)["status"] == "missing"

    D.rebuild_preflight(store, pf_config, "MNQ", DAY)
    assert D.preflight_view(store, pf_config, "MNQ", state)["status"] == "cached"

    _review(store, _sig(store, n=9), result_r=-1.0)       # journaling -> stale
    assert D.preflight_view(store, pf_config, "MNQ", state)["status"] == "stale"

    D.rebuild_preflight(store, pf_config, "MNQ", DAY)     # refresh button path
    assert D.preflight_view(store, pf_config, "MNQ", state)["status"] == "cached"

    pf_config.preflight.enabled = False
    assert D.preflight_view(store, pf_config, "MNQ", state)["status"] == "disabled"


# ── live path stays clean ────────────────────────────────────────────────────

def test_live_scan_gate_path_never_imports_preflight():
    """The TradingView -> features -> strategy -> risk gate decision path must
    not import (and therefore cannot wait on) the preflight module."""
    code = (
        "import sys; "
        "import futures_copilot.gate, futures_copilot.strategies.engine, "
        "futures_copilot.features.market_state, futures_copilot.data.collector; "
        "assert 'futures_copilot.preflight' not in sys.modules, 'live path imports preflight'; "
        "print('clean')"
    )
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "clean" in r.stdout
