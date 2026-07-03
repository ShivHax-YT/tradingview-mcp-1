"""Dashboard smoke: module imports cleanly outside `streamlit run`, and the
pure data helpers serve a seeded store. No server is started."""

import pytest
import time

pytest.importorskip("streamlit")

from futures_copilot.dashboard import data as D              # noqa: E402
from futures_copilot.gate import evaluate                    # noqa: E402
from futures_copilot.strategies import scan                  # noqa: E402

from .strategy_fixtures import seed_long_trap                # noqa: E402


def test_app_module_imports_without_runtime():
    import futures_copilot.dashboard.app as app
    assert callable(app.main)
    assert app._under_streamlit() is False        # bare import must not launch UI


def test_data_helpers_full_path(config, store):
    seed_long_trap(store)
    result = scan(store, config, "MNQ", persist=True)
    evaluate(result.state, result.candidates, store, config, persist=True)

    ov = D.load_overview(store, "MNQ")
    assert ov["state"]["symbol"] == "MNQ"
    assert ov["signals"] and ov["candidates"]

    gv = D.gate_view(ov["latest_signal"])
    assert gv["decision"] in ("LONG", "SHORT", "REJECT")
    assert gv["checklist"]

    ladder = D.levels_ladder(ov["state"])
    prices = [p for _, p, _ in ladder]
    assert prices == sorted(prices, reverse=True)          # ladder is top-down
    assert any("Prior day" in n for n, _, _ in ladder)
    assert any(src == "15m" for _, _, src in ladder)       # provenance surfaces

    sims = D.similar_for_signal(store, ov["latest_signal"])
    assert isinstance(sims, list)

    fresh, age = D.freshness(ov["state_row"])
    assert fresh in ("live", "stale") and age is not None


def test_gate_view_handles_empty():
    gv = D.gate_view(None)
    assert gv["decision"] == "WAIT" and gv["candidate"] is None


def test_candles_fig_optional_plotly(config, store):
    """Chart helper degrades to None without plotly instead of crashing."""
    seed_long_trap(store)
    from futures_copilot.dashboard.app import _candles_fig
    result = scan(store, config, "MNQ", persist=False)
    df5 = store.get_candles_df("MNQ", "5m", limit=50)
    fig = _candles_fig(df5, result.state.model_dump())
    try:
        import plotly  # noqa: F401
        assert fig is not None
    except ImportError:
        assert fig is None


def test_dashboard_executes_headless_via_apptest(config, store, tmp_path):
    """Full UI execution under Streamlit's AppTest: the entire main() renders
    against a seeded db with a gated LONG — any exception fails the test."""
    from streamlit.testing.v1 import AppTest

    seed_long_trap(store)
    result = scan(store, config, "MNQ", persist=True)
    evaluate(result.state, result.candidates, store, config, persist=True)
    # point a fresh config at this test db
    config.app.db_path = str(store.db_path)

    from futures_copilot.dashboard.app import main
    at = AppTest.from_function(main)
    at.session_state["_config"] = config
    at.run(timeout=60)

    assert not at.exception, f"dashboard raised: {at.exception}"
    md = " ".join(str(m.value) for m in at.markdown)
    assert "Futures Copilot" in md
    assert "LONG" in md                          # decision banner rendered
    assert "Level ladder" in md
    assert len(at.tabs) >= 6


def test_dashboard_live_mode_controls_render(config, store):
    from streamlit.testing.v1 import AppTest

    seed_long_trap(store)
    result = scan(store, config, "MNQ", persist=True)
    evaluate(result.state, result.candidates, store, config, persist=True)
    config.app.db_path = str(store.db_path)

    from futures_copilot.dashboard.app import main
    at = AppTest.from_function(main)
    at.session_state["_config"] = config
    at.session_state["live_enabled"] = True
    at.session_state["live_visual_interval"] = 10
    at.session_state["live_scan_interval"] = 60
    at.session_state["live_last_collect_ts"] = time.time()
    at.session_state["live_status"] = "test live status"
    at.run(timeout=60)

    assert not at.exception, f"dashboard raised: {at.exception}"
    md = " ".join(str(m.value) for m in at.markdown)
    assert "Live Mode" in md
    assert "test live status" in md
