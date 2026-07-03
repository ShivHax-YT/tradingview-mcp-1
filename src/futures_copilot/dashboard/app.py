"""Futures AI Chart Copilot — local dashboard.

Run:  streamlit run src/futures_copilot/dashboard/app.py
(or `copilot dashboard`)

Read + journal only. No alerts, no broker, no execution buttons — the app
shows what Python computed and lets you journal what YOU decided.
"""

from __future__ import annotations

import json

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

    import streamlit as st

    from futures_copilot.dashboard.charts import candles_fig as _candles_fig
    from futures_copilot.dashboard import data as D
    from futures_copilot.dashboard.theme import CSS
    from futures_copilot.packet import packet_from_latest, render_markdown, write_packet

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
        if st.button("Refresh data", use_container_width=True, key="refresh_data"):
            with st.spinner("Pulling TradingView bars and rescanning..."):
                try:
                    st.session_state["refresh_status"] = _refresh_symbol(config, symbol)
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

        # ── header strip ─────────────────────────────────────────────────────
        h1, h2, h3, h4 = st.columns([2.4, 1.6, 1.6, 3.2])
        with h1:
            if state:
                st.markdown(f'<div class="microlabel">{symbol} · {state["trading_day"]}</div>'
                            f'<div class="bigprice">{state["current_price"]:,.2f}</div>',
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
                sub = (f'<span class="sub">{c.get("setup_type")} · grade {c.get("grade")} · '
                       f'RR {c.get("rr", 0):.2f} · {D.fmt_ts(c.get("confirmed_close_ts"))}</span>')
            st.markdown(f'<div class="decision {dec}">{dec}{sub}</div>', unsafe_allow_html=True)

        if not state:
            st.info("No market state yet. Run `copilot backfill` then `copilot scan --symbol "
                    f"{symbol}` and refresh.")
            return

        st.write("")
        tabs = st.tabs(["Overview", "Signals", "Journal", "Mistakes", "Memory", "Packet"])

        # ── overview ─────────────────────────────────────────────────────────
        with tabs[0]:
            left, right = st.columns([2.5, 1.25], gap="small")
            with left:
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Price · 5m</div>', unsafe_allow_html=True)
                    df5 = store.get_candles_df(symbol, "5m", limit=180)
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
                                f'<span class="chip {side}"><span class="dot"></span>{c["direction"]}</span>'
                                f'&nbsp; {c["strategy_name"]} · grade {c["grade"]} · swept '
                                f'{(cj.get("context") or {}).get("swept_level", "—")}</span>'
                                f'<span class="v">entry {cj.get("entry_ref")} · stop {cj.get("stop")} '
                                f'· tgt {cj.get("target")} · RR {cj.get("rr", 0):.2f}</span></div>',
                                unsafe_allow_html=True)
                    else:
                        st.markdown('<div class="stat"><span class="k">no candidates in the last scans'
                                    '</span><span class="v muted">WAIT</span></div>',
                                    unsafe_allow_html=True)
            with right:
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Level ladder</div>', unsafe_allow_html=True)
                    px = state["current_price"]
                    rows = D.levels_ladder(state)
                    shown_px = False
                    for name, lvl, src in rows:
                        if not shown_px and lvl <= px:
                            st.markdown(f'<div class="lvl"><span class="name" style="color:#46e264">'
                                        f'▸ price</span><span class="px" style="color:#46e264">{px:,.2f}'
                                        f'</span><span class="src">now</span></div>', unsafe_allow_html=True)
                            shown_px = True
                        st.markdown(f'<div class="lvl"><span class="name">{name}</span>'
                                    f'<span class="px">{lvl:,.2f}</span>'
                                    f'<span class="src">{src}</span></div>', unsafe_allow_html=True)
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
                                    f'<span class="v {tone}">{v}</span></div>', unsafe_allow_html=True)
                with st.container(border=True):
                    st.markdown('<div class="microlabel">Bar coverage</div>', unsafe_allow_html=True)
                    for c in ov["coverage"]:
                        st.markdown(f'<div class="stat"><span class="k">{c["timeframe"]}</span>'
                                    f'<span class="v">{c["bars"]} bars · to {c["last"]}</span></div>',
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
                            f'<div class="stat"><span class="k">#{srow["id"]} · {srow["trading_day"]} '
                            f'{srow["session"]} · {srow["setup"]}</span>'
                            f'<span class="v"><span class="chip {tone}"><span class="dot"></span>'
                            f'{srow["decision"]}</span></span></div>', unsafe_allow_html=True)
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
                                f'entry {cand.get("entry_lo")}–{cand.get("entry_hi")} · stop {cand.get("stop")} '
                                f'· target {cand.get("target")} ({cand.get("target_name")}) · '
                                f'RR {cand.get("rr", 0):.2f} · grade {cand.get("grade")}</span></div>',
                                unsafe_allow_html=True)
                            st.markdown(f'<div class="stat"><span class="k">confluences</span>'
                                        f'<span class="v">{", ".join(cand.get("confluences", []) or ["—"])}'
                                        f'</span></div>', unsafe_allow_html=True)
                        for chk in g["checklist"]:
                            cls = "pass" if chk.get("passed") else "fail"
                            st.markdown(f'<div class="chk {cls}"><span class="mark">'
                                        f'{"PASS" if chk.get("passed") else "FAIL"}</span>'
                                        f'<span>{chk.get("check")}</span>'
                                        f'<span class="why">{chk.get("detail")}</span></div>',
                                        unsafe_allow_html=True)
                        if g["invalidation"]:
                            st.markdown('<div class="microlabel" style="margin-top:.6rem">Invalidation</div>',
                                        unsafe_allow_html=True)
                            for inv in g["invalidation"]:
                                st.markdown(f'<div class="stat"><span class="k">✕ {inv}</span></div>',
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
                            st.markdown(f'<div class="stat"><span class="k">#{r["signal_id"]} · '
                                        f'{(r["notes"] or "")[:60]}</span>'
                                        f'<span class="v {tone}">{res}</span></div>', unsafe_allow_html=True)
                    else:
                        st.caption("no reviews yet")

        # ── mistakes ─────────────────────────────────────────────────────────
        with tabs[3]:
            with st.container(border=True):
                st.markdown('<div class="microlabel">Mistake ledger — what not to repeat</div>',
                            unsafe_allow_html=True)
                if ov["mistakes"]:
                    for m in ov["mistakes"]:
                        st.markdown(f'<div class="stat"><span class="k"><b>{m["tag"]}</b> — '
                                    f'{m["description"] or ""}</span>'
                                    f'<span class="v muted">{m["rule_update"] or ""}</span></div>',
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
                            st.markdown(f'<div class="stat"><span class="k">#{s_["signal_id"]} · '
                                        f'{s_["trading_day"]} {s_["session"]} · {s_["decision"]} · '
                                        f'grade {s_["grade"]} · RR {s_["rr"]} · swept {s_["swept_level"]}</span>'
                                        f'<span class="v {tone}">{oc_txt}</span></div>',
                                        unsafe_allow_html=True)
                    else:
                        st.caption("no similar setups on record yet — memory grows as you journal")
                else:
                    st.caption("no signals yet")

        # ── packet ───────────────────────────────────────────────────────────
        with tabs[5]:
            with st.container(border=True):
                st.markdown('<div class="microlabel">Claude prompt packet</div>', unsafe_allow_html=True)
                st.caption("Claude explains, grades, warns, journals. The decision field does not "
                           "exist in its output schema — the gate already decided.")
                try:
                    packet = packet_from_latest(store, config, symbol)
                    pcol1, pcol2 = st.columns([1, 1])
                    with pcol1:
                        if st.button("Write packet files (JSON + MD)"):
                            jp, mp = write_packet(packet, config)
                            st.success(f"written: {jp.name}, {mp.name} → {jp.parent}")
                        st.download_button("Download JSON",
                                           json.dumps(packet, indent=2, default=str),
                                           file_name="copilot_packet.json", mime="application/json")
                    with pcol2:
                        st.download_button("Download Markdown", render_markdown(packet),
                                           file_name="copilot_packet.md", mime="text/markdown")
                    view = st.radio("view", ["markdown", "json"], horizontal=True,
                                    label_visibility="collapsed", key="packet_view")
                    if view == "markdown":
                        st.markdown(render_markdown(packet))
                    else:
                        st.json(packet, expanded=False)
                except ValueError as e:
                    st.info(str(e))
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
