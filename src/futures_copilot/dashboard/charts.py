"""Dashboard chart helpers."""

from __future__ import annotations

from futures_copilot.dashboard import data as D


def candles_fig(df, state):
    """Legend-style candle chart (plotly optional; returns None if missing)."""
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
