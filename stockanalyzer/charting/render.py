"""Interactive plotly candlestick chart annotated with the engine's findings:
moving averages, support/resistance levels, trendlines, and a volume sub-panel.

Layout philosophy: the price panel is the hero. Indicator panels (volume, RSI,
MACD) are compact strips, and the *total* figure height grows with the number of
panels rather than squeezing price into a sliver — so a 4-panel intraday chart is
taller, not more cramped, than a 2-panel one.
"""
from __future__ import annotations

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..analysis.engine import TimeframeReport
from ..data.market_session import Session, is_intraday, sessions_for_index

# Per-panel pixel budgets. Row heights and the figure's total height are both
# derived from these, so price always gets a generous, fixed slice and the
# indicator strips stay readable no matter how many panels are present.
_PANEL_PX = {"price": 460, "volume": 88, "rsi": 120, "macd": 120}

_UP = "#26a69a"
_DOWN = "#ef5350"
_GRID = "rgba(120,130,140,0.18)"


def _shade_extended(fig: go.Figure, df) -> None:
    """Shade contiguous pre-market / after-hours blocks on an intraday chart.

    Only the first block is labelled; later blocks are shaded silently. (Passing
    annotation kwargs with no text makes plotly draw its "new text" placeholder —
    that was the stray label we used to see on multi-session intraday charts.)
    """
    sessions = sessions_for_index(df.index)
    idx = df.index
    start = None
    labelled = False
    for i, s in enumerate(sessions + [Session.REGULAR]):  # sentinel closes the last run
        extended = i < len(sessions) and s.is_extended
        if extended and start is None:
            start = i
        elif not extended and start is not None:
            note = {}
            if not labelled:
                note = dict(annotation_text="extended hrs",
                            annotation_position="top left",
                            annotation_font=dict(size=10, color="rgba(176,190,197,0.8)"))
                labelled = True
            fig.add_vrect(
                x0=idx[start], x1=idx[i - 1],
                fillcolor="rgba(150,150,150,0.16)", line_width=0, layer="below",
                row=1, col=1, **note,
            )
            start = None


def _price_tags(report: TimeframeReport, df) -> list[tuple[float, str, str, bool]]:
    """De-collided right-edge price tags: support/resistance levels plus the last
    price. Levels too close to each other (or to the last-price tag) are dropped so
    the labels never stack into an unreadable smear. Returns (price, text, color,
    is_last) sorted strongest-first for the kept levels."""
    if not len(df):
        return []
    last = float(df["close"].iloc[-1])
    span = float(df["high"].max() - df["low"].min()) or last or 1.0
    gap = span * 0.045  # minimum vertical separation, in price units

    tags: list[tuple[float, str, str, bool]] = [(last, f"{last:.2f}", "#eceff1", True)]
    kept_prices = [last]
    # Prefer the levels nearest the current price (most actionable), strongest first.
    for lv in sorted(report.levels, key=lambda L: (abs(L.price - last), -L.touches)):
        if any(abs(lv.price - p) < gap for p in kept_prices):
            continue
        tag = "R" if lv.kind == "resistance" else "S"
        color = _DOWN if lv.kind == "resistance" else _UP
        tags.append((lv.price, f"{tag} {lv.price:.2f}", color, False))
        kept_prices.append(lv.price)
        if len(kept_prices) >= 7:  # cap clutter regardless of how many levels exist
            break
    return tags


def candlestick_figure(report: TimeframeReport, title: str = "") -> go.Figure:
    df = report.df
    has_rsi = "rsi" in df and df["rsi"].notna().any()
    has_macd = "macd" in df and df["macd"].notna().any()

    # Build the panel stack: price always; volume always; RSI/MACD only if present.
    specs = [("price", _PANEL_PX["price"]), ("volume", _PANEL_PX["volume"])]
    if has_rsi:
        specs.append(("rsi", _PANEL_PX["rsi"]))
    if has_macd:
        specs.append(("macd", _PANEL_PX["macd"]))
    rows = len(specs)
    row_of = {name: i + 1 for i, (name, _) in enumerate(specs)}
    total_px = sum(px for _, px in specs)
    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True,
        row_heights=[px for _, px in specs], vertical_spacing=0.025,
    )

    fig.add_trace(
        go.Candlestick(
            x=df.index, open=df["open"], high=df["high"],
            low=df["low"], close=df["close"], name="price",
            increasing_line_color=_UP, decreasing_line_color=_DOWN,
            increasing_fillcolor=_UP, decreasing_fillcolor=_DOWN,
            line=dict(width=1),
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
                           line=dict(width=1.2, color=color)),
                row=1, col=1,
            )

    # Support/resistance as horizontal guides (labels handled separately, below,
    # so close levels can't pile their text on top of each other).
    for lv in report.levels:
        color = _DOWN if lv.kind == "resistance" else _UP
        fig.add_hline(y=lv.price, line=dict(color=color, width=1, dash="dot"),
                      opacity=0.55, row=1, col=1)

    # Trendlines projected across the visible range.
    n = len(df)
    for key, line in report.trendlines.items():
        if line.points < 2:
            continue
        y0, y1 = line.value_at(0), line.value_at(n - 1)
        color = _DOWN if key == "resistance" else _UP
        fig.add_trace(
            go.Scatter(x=[df.index[0], df.index[-1]], y=[y0, y1], mode="lines",
                       name=f"{key} TL", line=dict(color=color, width=1.5, dash="dash")),
            row=1, col=1,
        )

    # Volume colored by bar direction.
    vol_colors = [_UP if c >= o else _DOWN for o, c in zip(df["open"], df["close"])]
    fig.add_trace(
        go.Bar(x=df.index, y=df["volume"], name="volume", marker_color=vol_colors,
               marker_line_width=0, showlegend=False),
        row=row_of["volume"], col=1,
    )

    # RSI(14) panel with 70/30 overbought/oversold guides.
    if has_rsi:
        r = row_of["rsi"]
        fig.add_trace(
            go.Scatter(x=df.index, y=df["rsi"], mode="lines", name="RSI",
                       line=dict(width=1.2, color="#26c6da"), showlegend=False),
            row=r, col=1,
        )
        for lvl, col in ((70, _DOWN), (30, _UP)):
            fig.add_hline(y=lvl, line=dict(color=col, width=0.8, dash="dot"),
                          opacity=0.6, row=r, col=1)
        fig.update_yaxes(range=[0, 100], row=r, col=1)

    # MACD panel: line, signal, and histogram.
    if has_macd:
        r = row_of["macd"]
        if "macd_hist" in df:
            hcol = [_UP if h >= 0 else _DOWN for h in df["macd_hist"].fillna(0)]
            fig.add_trace(
                go.Bar(x=df.index, y=df["macd_hist"], name="hist",
                       marker_color=hcol, marker_line_width=0, showlegend=False),
                row=r, col=1,
            )
        fig.add_trace(
            go.Scatter(x=df.index, y=df["macd"], mode="lines", name="MACD",
                       line=dict(width=1.2, color="#42a5f5"), showlegend=False),
            row=r, col=1,
        )
        if "macd_signal" in df:
            fig.add_trace(
                go.Scatter(x=df.index, y=df["macd_signal"], mode="lines", name="signal",
                           line=dict(width=1.2, color="#ffa726"), showlegend=False),
                row=r, col=1,
            )

    # Current-price reference line (label handled by the pill tag below).
    if len(df):
        fig.add_hline(y=float(df["close"].iloc[-1]),
                      line=dict(color="rgba(207,216,220,0.6)", width=1, dash="dot"),
                      row=1, col=1)

    # Right-edge price tags: de-collided S/R labels + the current price, drawn as
    # paper-anchored annotations so they sit cleanly in the right margin. The last
    # price gets a filled pill; levels get plain colored text.
    for price, text, color, is_last in _price_tags(report, df):
        fig.add_annotation(
            xref="x domain", x=1.0, xanchor="left", xshift=6,
            yref="y", y=price, yanchor="middle", showarrow=False,
            text=f" {text} ", font=dict(size=11, color="#102027" if is_last else color),
            bgcolor=color if is_last else None,
            borderpad=2 if is_last else 0, row=1, col=1,
        )

    fig.update_layout(
        # Title lives entirely in the top margin (yanchor bottom at the plot's top
        # edge); the legend sits just *inside* the plot below it, so the two never
        # share a line. Translucent legend background keeps candles legible behind.
        title=dict(text=title, x=0.006, y=1.0, xref="paper", yref="paper",
                   xanchor="left", yanchor="bottom", font=dict(size=15)),
        template="plotly_dark",
        paper_bgcolor="#0e1117", plot_bgcolor="#0e1117",
        xaxis_rangeslider_visible=False,
        height=total_px,
        margin=dict(l=8, r=78, t=34, b=24),
        legend=dict(orientation="h", y=0.998, x=0.0, xanchor="left", yanchor="top",
                    font=dict(size=10.5), bgcolor="rgba(14,17,23,0.55)",
                    borderwidth=0),
        bargap=0.15,
        hovermode="x unified",
        # Preserve user zoom/pan across re-renders (critical for live mode).
        uirevision=title or "chart",
        # Defaults: left-drag PANS (not zoom); double-click resets + autoscales.
        dragmode="pan",
    )
    fig.update_yaxes(title_text="price", title_font=dict(size=11),
                     gridcolor=_GRID, zeroline=False, row=1, col=1)
    fig.update_yaxes(title_text="vol", title_font=dict(size=11),
                     gridcolor=_GRID, zeroline=False, row=row_of["volume"], col=1)
    if has_rsi:
        fig.update_yaxes(title_text="RSI", title_font=dict(size=11),
                         gridcolor=_GRID, row=row_of["rsi"], col=1)
    if has_macd:
        fig.update_yaxes(title_text="MACD", title_font=dict(size=11),
                         gridcolor=_GRID, zeroline=False, row=row_of["macd"], col=1)
    fig.update_xaxes(gridcolor=_GRID, zeroline=False)
    # Frame the price panel to the CANDLES (not the overlays). Autoranging over
    # MAs/trendlines/projected levels lets a far-off line (e.g. a steep projected
    # trendline) blow the scale and squish the actual price action into an
    # unreadable band. Anchor the range to the candle high/low so candles are
    # always legible; off-scale overlays simply clip.
    if len(df):
        lo, hi = float(df["low"].min()), float(df["high"].max())
        if hi > lo:
            pad = (hi - lo) * 0.06
            fig.update_yaxes(range=[lo - pad, hi + pad], fixedrange=False,
                             row=1, col=1)
        else:
            fig.update_yaxes(autorange=True, fixedrange=False, row=1, col=1)
    else:
        fig.update_yaxes(autorange=True, fixedrange=False, row=1, col=1)

    # Collapse non-trading time so candles are contiguous (like a real trading site),
    # instead of leaving huge empty gaps for overnight/weekends on the datetime axis.
    rangebreaks = [dict(bounds=["sat", "mon"])]            # hide weekends
    if is_intraday(df):
        # Keep extended-hours window 04:00–20:00 ET; hide the overnight 20:00–04:00.
        rangebreaks.append(dict(bounds=[20, 4], pattern="hour"))
    fig.update_xaxes(rangebreaks=rangebreaks)
    return fig
