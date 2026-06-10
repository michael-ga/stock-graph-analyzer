"""Interactive plotly candlestick chart annotated with the engine's findings:
moving averages, support/resistance levels, trendlines, and a volume sub-panel.
"""
from __future__ import annotations

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..analysis.engine import TimeframeReport
from ..data.market_session import Session, is_intraday, sessions_for_index


def _shade_extended(fig: go.Figure, df) -> None:
    """Shade contiguous pre-market / after-hours blocks on an intraday chart."""
    sessions = sessions_for_index(df.index)
    idx = df.index
    start = None
    annotated = False
    for i, s in enumerate(sessions + [Session.REGULAR]):  # sentinel closes the last run
        extended = i < len(sessions) and s.is_extended
        if extended and start is None:
            start = i
        elif not extended and start is not None:
            fig.add_vrect(
                x0=idx[start], x1=idx[i - 1],
                fillcolor="rgba(150,150,150,0.18)", line_width=0, layer="below",
                annotation_text=("extended hours" if not annotated else None),
                annotation_position="top left", row=1, col=1,
            )
            annotated = True
            start = None


def candlestick_figure(report: TimeframeReport, title: str = "") -> go.Figure:
    df = report.df
    has_rsi = "rsi" in df and df["rsi"].notna().any()
    has_macd = "macd" in df and df["macd"].notna().any()

    # Rows: price (+MAs) · volume · RSI · MACD — only add the indicator panels we have.
    specs = [("price", 0.50), ("volume", 0.14)]
    if has_rsi:
        specs.append(("rsi", 0.18))
    if has_macd:
        specs.append(("macd", 0.18))
    rows = len(specs)
    row_of = {name: i + 1 for i, (name, _) in enumerate(specs)}
    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True,
        row_heights=[h for _, h in specs], vertical_spacing=0.03,
    )

    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["open"], high=df["high"],
            low=df["low"], close=df["close"], name="price",
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        ),
        row=1, col=1,
    )

    if is_intraday(df):
        _shade_extended(fig, df)

    for ma, color in (("sma20", "#42a5f5"), ("sma50", "#ffa726"),
                      ("sma200", "#ab47bc")):
        if ma in df and df[ma].notna().any():
            fig.add_trace(
                go.Scatter(x=df.index, y=df[ma], mode="lines", name=ma.upper(),
                           line=dict(width=1, color=color)),
                row=1, col=1,
            )

    # Support/resistance as horizontal lines.
    for lv in report.levels:
        color = "#ef5350" if lv.kind == "resistance" else "#26a69a"
        fig.add_hline(
            y=lv.price, line=dict(color=color, width=1, dash="dot"),
            annotation_text=f"{lv.kind[:3].upper()} {lv.price:.2f} ({lv.touches}x)",
            annotation_position="right", row=1, col=1,
        )

    # Trendlines projected across the visible range.
    n = len(df)
    for key, line in report.trendlines.items():
        if line.points < 2:
            continue
        y0, y1 = line.value_at(0), line.value_at(n - 1)
        color = "#ef5350" if key == "resistance" else "#26a69a"
        fig.add_trace(
            go.Scatter(x=[df.index[0], df.index[-1]], y=[y0, y1], mode="lines",
                       name=f"{key} TL", line=dict(color=color, width=1.5, dash="dash")),
            row=1, col=1,
        )

    # Volume colored by bar direction.
    vol_colors = ["#26a69a" if c >= o else "#ef5350"
                  for o, c in zip(df["open"], df["close"])]
    fig.add_trace(
        go.Bar(x=df.index, y=df["volume"], name="volume", marker_color=vol_colors,
               showlegend=False),
        row=row_of["volume"], col=1,
    )

    # RSI(14) panel with 70/30 overbought/oversold guides.
    if has_rsi:
        r = row_of["rsi"]
        fig.add_trace(
            go.Scatter(x=df.index, y=df["rsi"], mode="lines", name="RSI",
                       line=dict(width=1, color="#26c6da"), showlegend=False),
            row=r, col=1,
        )
        for lvl, col in ((70, "#ef5350"), (30, "#26a69a")):
            fig.add_hline(y=lvl, line=dict(color=col, width=0.8, dash="dot"),
                          row=r, col=1)
        fig.update_yaxes(range=[0, 100], row=r, col=1)

    # MACD panel: line, signal, and histogram.
    if has_macd:
        r = row_of["macd"]
        if "macd_hist" in df:
            hcol = ["#26a69a" if h >= 0 else "#ef5350" for h in df["macd_hist"].fillna(0)]
            fig.add_trace(
                go.Bar(x=df.index, y=df["macd_hist"], name="hist",
                       marker_color=hcol, showlegend=False),
                row=r, col=1,
            )
        fig.add_trace(
            go.Scatter(x=df.index, y=df["macd"], mode="lines", name="MACD",
                       line=dict(width=1, color="#42a5f5"), showlegend=False),
            row=r, col=1,
        )
        if "macd_signal" in df:
            fig.add_trace(
                go.Scatter(x=df.index, y=df["macd_signal"], mode="lines", name="signal",
                           line=dict(width=1, color="#ffa726"), showlegend=False),
                row=r, col=1,
            )

    # Current-price reference line + right-edge annotation (like every trading site).
    if len(df):
        last = float(df["close"].iloc[-1])
        fig.add_hline(
            y=last, line=dict(color="#cfd8dc", width=1, dash="dot"),
            annotation_text=f"  {last:.2f}", annotation_position="right",
            annotation_font_color="#cfd8dc", row=1, col=1,
        )

    fig.update_layout(
        title=title,
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        height=620,
        margin=dict(l=10, r=70, t=40, b=10),
        legend=dict(orientation="h", y=1.02, x=0),
        # Preserve user zoom/pan across re-renders (critical for live mode).
        uirevision=title or "chart",
        # Defaults: left-drag PANS (not zoom); double-click resets + autoscales.
        dragmode="pan",
    )
    fig.update_yaxes(title_text="price", row=1, col=1)
    fig.update_yaxes(title_text="vol", row=row_of["volume"], col=1)
    if has_rsi:
        fig.update_yaxes(title_text="RSI", row=row_of["rsi"], col=1)
    if has_macd:
        fig.update_yaxes(title_text="MACD", row=row_of["macd"], col=1)
    # Autoscale the price panel by default (RSI stays pinned 0–100 above).
    fig.update_yaxes(autorange=True, fixedrange=False, row=1, col=1)

    # Collapse non-trading time so candles are contiguous (like a real trading site),
    # instead of leaving huge empty gaps for overnight/weekends on the datetime axis.
    rangebreaks = [dict(bounds=["sat", "mon"])]            # hide weekends
    if is_intraday(df):
        # Keep extended-hours window 04:00–20:00 ET; hide the overnight 20:00–04:00.
        rangebreaks.append(dict(bounds=[20, 4], pattern="hour"))
    fig.update_xaxes(rangebreaks=rangebreaks)
    return fig
