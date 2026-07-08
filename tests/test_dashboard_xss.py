"""XSS regression: journal/mistake/notes text must never reach the browser as
raw HTML. Malicious payloads are inserted through the same store methods the
dashboard forms use, then the FULL dashboard is executed under AppTest and
every rendered markdown body is checked for unescaped tags."""

import pytest

pytest.importorskip("streamlit")

from futures_copilot.dashboard.safe_html import h                    # noqa: E402
from futures_copilot.gate import evaluate                            # noqa: E402
from futures_copilot.strategies import scan                          # noqa: E402

from .strategy_fixtures import seed_long_trap                        # noqa: E402

SCRIPT_PAYLOAD = '<script>window.__xss_pwned = true</script>'
IMG_PAYLOAD = '<img src=x onerror="window.__xss_pwned = true">'


def test_h_escapes_everything():
    assert h(None) == ""
    assert h(123) == "123"
    assert h("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"
    assert h('a"b') == "a&quot;b"
    assert "<" not in h(SCRIPT_PAYLOAD)
    assert "<" not in h(IMG_PAYLOAD)


def test_dashboard_renders_hostile_journal_text_escaped(config, store):
    from streamlit.testing.v1 import AppTest

    seed_long_trap(store)
    result = scan(store, config, "MNQ", persist=True)
    gate = evaluate(result.state, result.candidates, store, config, persist=True)

    # hostile input through the exact write paths the dashboard forms use
    store.add_mistake(SCRIPT_PAYLOAD, IMG_PAYLOAD, SCRIPT_PAYLOAD, "MNQ")
    sig_id = gate.chosen.signal_id if gate.chosen else gate.evaluations[0].signal_id
    store.add_trade_review(signal_id=sig_id, symbol="MNQ", taken=True,
                           result_r=1.0, notes=IMG_PAYLOAD)
    store.set_daily_bias(result.state.trading_day, "MNQ", SCRIPT_PAYLOAD, "notes")

    config.app.db_path = str(store.db_path)

    from futures_copilot.dashboard.app import main
    at = AppTest.from_function(main)
    at.session_state["_config"] = config
    at.run(timeout=60)
    assert not at.exception, f"dashboard raised: {at.exception}"

    # Only markdown rendered with unsafe_allow_html=True reaches the browser as
    # raw HTML; everything else Streamlit sanitizes client-side. The packet tab
    # (plain st.markdown) may legitimately carry the payload as inert text.
    html_sinks = [str(m.value) for m in at.markdown
                  if getattr(m.proto, "allow_html", False)]
    assert html_sinks, "expected at least one unsafe_allow_html markdown element"
    rendered = " ".join(html_sinks)
    assert "<script>" not in rendered, "raw <script> reached an unsafe_allow_html sink"
    assert "<img src=x" not in rendered, "raw <img onerror> reached an unsafe_allow_html sink"
    # the escaped forms SHOULD be visible somewhere (mistake ledger renders tags)
    assert "&lt;script&gt;" in rendered
