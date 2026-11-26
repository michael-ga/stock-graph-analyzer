"""Trade Radar — a light, modern always-on-top window for the live setup watcher.

Reads .local/live/state.json (watch_setups.py, every ~20s) and .local/live/position.json (track_position.py).
No extra packages: tkinter only.   python radar_gui.py   (or pythonw for no console)

Layout adapts to the window size, so nothing is cut off when it isn't maximized:
- the header and footer stay fixed; everything in between scrolls (mouse wheel) when it doesn't fit;
- narrow (< 860 px): one column, the ticker cards flow into 1-3 columns as the window widens;
- wide (>= 860 px): the trade card and open position sit on the left, the ticker cards fill a grid on the right;
- text wraps to the current width; the VWAP gauges redraw on resize;
- the window only rebuilds when the data changes, so scrolling and resizing don't flicker.
"""
import json, os, sys, time, datetime as dt, tkinter as tk, winsound
sys.path.insert(0, os.path.dirname(__file__))
from common import STATE as _STATE, STATE_DIR, FEE, alerts_log
POSITION = os.environ.get("RADAR_POSITION") or os.path.join(STATE_DIR, "position.json")
NEWS = os.path.join(STATE_DIR, "news.json")

STATE = os.environ.get("RADAR_STATE") or _STATE
BG, CARD, LINE, CHIP = "#f4f5f8", "#ffffff", "#e3e6eb", "#eef0f4"
INK, SUB, MUTED = "#111418", "#5b6270", "#9aa1ad"
GREEN, GREEN_BG, RED, RED_BG, AMBER, AMBER_BG, BLUE, BLUE_BG = (
    "#16a34a", "#ecfdf3", "#dc2626", "#fef2f2", "#d97706", "#fffbeb", "#2563eb", "#eff6ff")
F = "Segoe UI"
ACTIVE_MIN = 10            # a signal stays "ready" for 10 minutes
PAD = 14                   # outer margin
WIDE = 860                 # from this width: trade card left, ticker grid right
LEFT_W = 400               # left pane width in wide mode
CARD_MIN = 290             # minimum width of one ticker card


class Radar(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Trade Radar"); self.configure(bg=BG); self.geometry("460x820+40+30"); self.minsize(340, 420)
        self.attributes("-topmost", True)
        self.seen, self.dismissed = set(), set()
        self.sig_last, self.layout_key, self.tiles, self._resize_job = None, None, [], None

        # ---- fixed header ----
        top = tk.Frame(self, bg=BG); top.pack(fill="x", padx=PAD, pady=(12, 2))
        tk.Label(top, text="Trade Radar", font=(F, 15, "bold"), fg=INK, bg=BG).pack(side="left")
        self.dot = tk.Label(top, text="●", font=(F, 11), fg=MUTED, bg=BG); self.dot.pack(side="right")
        self.clock = tk.Label(top, text="--:--", font=(F, 10, "bold"), fg=SUB, bg=BG); self.clock.pack(side="right", padx=6)
        meta = tk.Frame(self, bg=BG); meta.pack(fill="x", padx=PAD, pady=(0, 6))
        self.feed = self.pill(meta, "", MUTED, CHIP); self.feed.pack(side="left")
        self.vix = self.pill(meta, "", SUB, CHIP); self.vix.pack(side="left", padx=6)

        # ---- fixed footer (packed before the body so it is never pushed off-screen) ----
        foot = tk.Frame(self, bg=BG); foot.pack(side="bottom", fill="x", padx=PAD, pady=(4, 10))
        self.news = tk.Label(foot, text="", font=(F, 8), fg=SUB, bg=BG, anchor="w", justify="left")
        self.news.pack(fill="x")
        bar = tk.Frame(foot, bg=BG); bar.pack(fill="x", pady=(4, 0))
        self.note = tk.Label(bar, text="", font=(F, 9), fg=AMBER, bg=BG, anchor="w", justify="left")
        self.note.pack(side="left", fill="x", expand=True)
        self.pin = tk.BooleanVar(value=True)
        tk.Checkbutton(bar, text="On top", variable=self.pin, command=lambda: self.attributes("-topmost", self.pin.get()),
                       font=(F, 9), fg=SUB, bg=BG, activebackground=BG, selectcolor=CARD, bd=0).pack(side="right")
        self.news_items, self.foot_w = [], 440
        foot.bind("<Configure>", self.on_foot)

        # ---- scrollable body ----
        wrap = tk.Frame(self, bg=BG); wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        self.sb = tk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview, width=12)
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=BG)
        self.body_id = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.update_scroll())
        self.canvas.bind("<Configure>", self.on_resize)
        self.canvas.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self.on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))

        self.left = tk.Frame(self.body, bg=BG)
        self.pos = tk.Frame(self.left, bg=BG); self.pos.pack(fill="x")
        self.banner = tk.Frame(self.left, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        self.banner.pack(fill="x", pady=(8, 0))
        self.alerts = tk.Frame(self.left, bg=CARD, highlightthickness=1, highlightbackground=LINE)
        self.alerts.pack(fill="x", pady=(8, 0))
        self.cards = tk.Frame(self.body, bg=BG)
        self.after(200, self.refresh)

    # ---------- layout ----------
    def width(self):
        return max(self.canvas.winfo_width(), 300)

    def on_resize(self, e):
        self.canvas.itemconfigure(self.body_id, width=e.width)
        if self._resize_job: self.after_cancel(self._resize_job)
        self._resize_job = self.after(60, self.relayout)

    def relayout(self):
        """Place the panes and the ticker grid for the current width (no rebuild)."""
        self._resize_job = None
        w = self.width(); wide = w >= WIDE
        right_w = (w - 2 * PAD - LEFT_W - PAD) if wide else (w - 2 * PAD)
        cols = max(1, min(len(self.tiles) or 1, right_w // CARD_MIN))
        key = (wide, cols)
        if key != self.layout_key:
            self.layout_key = key
            self.left.grid_forget(); self.cards.grid_forget()
            for i in range(3): self.body.columnconfigure(i, weight=0, minsize=0)
            if wide:
                self.body.columnconfigure(0, minsize=LEFT_W); self.body.columnconfigure(1, weight=1)
                self.left.grid(row=0, column=0, sticky="new", padx=(PAD, 0), pady=(0, 8))
                self.cards.grid(row=0, column=1, sticky="new", padx=PAD, pady=(0, 8))
            else:
                self.body.columnconfigure(0, weight=1)
                self.left.grid(row=0, column=0, sticky="new", padx=PAD)
                self.cards.grid(row=1, column=0, sticky="new", padx=PAD, pady=(8, 8))
            for i in range(4): self.cards.columnconfigure(i, weight=0, uniform="")
            for i in range(cols): self.cards.columnconfigure(i, weight=1, uniform="card")
        for n, t in enumerate(self.tiles):
            t.grid(row=n // cols, column=n % cols, sticky="nsew",
                   padx=(0 if n % cols == 0 else 8, 0), pady=(0 if n < cols else 8, 0))
        self.update_scroll()

    def on_foot(self, e):
        self.foot_w = e.width
        self.note.configure(wraplength=max(e.width - 90, 150))
        self.render_news()

    def render_news(self):
        """One line per headline, cut to the window width, so the footer never eats the body."""
        if not self.news_items: return
        n = max(int((self.foot_w - 50) / 5.4), 30)
        lines = [f"{h['t']}  " + (h["title"] if len(h["title"]) <= n else h["title"][:n - 1].rstrip() + "…")
                 for h in self.news_items]
        self.news.configure(text="NEWS\n" + "\n".join(lines))

    def update_scroll(self):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        need = self.body.winfo_reqheight() > self.canvas.winfo_height() + 2
        if need and not self.sb.winfo_ismapped(): self.sb.pack(side="right", fill="y", before=self.canvas)
        elif not need and self.sb.winfo_ismapped(): self.sb.pack_forget(); self.canvas.yview_moveto(0)

    def on_wheel(self, e):
        if self.sb.winfo_ismapped(): self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units")

    # ---------- small widgets ----------
    def clear(self, w):
        for c in w.winfo_children(): c.destroy()

    @staticmethod
    def pill(parent, text, fg, bg, bold=False):
        return tk.Label(parent, text=text, font=(F, 8, "bold" if bold else "normal"), fg=fg, bg=bg, padx=7, pady=2)

    @staticmethod
    def wrapping(lbl, parent, pad):
        """Keep a label's wrap width in step with its container."""
        parent.bind("<Configure>", lambda e: lbl.configure(wraplength=max(e.width - pad, 120)), add="+")
        return lbl

    def button(self, parent, text, cmd, fg):
        return tk.Button(parent, text=text, command=cmd, font=(F, 9, "bold"), fg=fg, bg=CARD, activebackground=LINE,
                         relief="flat", bd=0, padx=12, pady=5, cursor="hand2", highlightthickness=1, highlightbackground=LINE)

    # ---------- rendering ----------
    def render_banner(self, sig):
        b = self.banner; self.clear(b)
        if sig is None:
            b.configure(bg=CARD, highlightbackground=LINE)
            tk.Label(b, text="No setup", font=(F, 12, "bold"), fg=SUB, bg=CARD).pack(anchor="w", padx=14, pady=(12, 0))
            self.wrapping(tk.Label(b, text="Waiting for a bounce to VWAP that fails (short) or a VWAP reclaim (long).",
                                   font=(F, 9), fg=MUTED, bg=CARD, justify="left"), b, 30).pack(anchor="w", padx=14, pady=(2, 12))
            return
        short = sig["direction"].startswith("SHORT"); col, bgc = (RED, RED_BG) if short else (GREEN, GREEN_BG)
        risky = sig.get("risky")
        if risky: col, bgc = AMBER, AMBER_BG
        b.configure(bg=bgc, highlightbackground=col)
        tk.Frame(b, bg=col, height=4).pack(fill="x")
        head = tk.Frame(b, bg=bgc); head.pack(fill="x", padx=14, pady=(10, 2))
        grade = (sig.get("grade") or "").split(":")[0]          # evidence grade (vwap_rules.EVIDENCE)
        title = ("⚠ RISKY · counter-trend · not backtested · $300 MAX" if risky else
                 "TRADE READY · TESTED SETUP (2-yr study)" if sig.get("tested") else
                 f"TRADE READY · {grade}" if grade else "TRADE READY")
        tk.Label(head, text=f"{sig['t'][:5]} · {self.age(sig['t'])}", font=(F, 9), fg=SUB, bg=bgc).pack(side="right")
        self.wrapping(tk.Label(head, text=title, font=(F, 9, "bold"), fg=col, bg=bgc, justify="left"), head, 110).pack(side="left")
        tk.Label(b, text=f"BUY {sig['etf']}", font=(F, 20, "bold"), fg=INK, bg=bgc).pack(anchor="w", padx=14)
        tk.Label(b, text=("short " if short else "long ") + sig["und"] + f" · R:R {sig['rr']:.1f}", font=(F, 10), fg=SUB, bg=bgc).pack(anchor="w", padx=14)
        grid = tk.Frame(b, bg=bgc); grid.pack(fill="x", padx=14, pady=8)
        for i, (k, o, v, c) in enumerate((("ENTRY", "buy limit", sig["entry"], INK), ("STOP", "stop-market", sig["stop"], RED),
                                          ("TARGET", "sell limit", sig["target"], GREEN))):
            cell = tk.Frame(grid, bg=CARD, highlightthickness=1, highlightbackground=LINE)
            cell.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 6, 0))
            grid.columnconfigure(i, weight=1, uniform="o")
            tk.Label(cell, text=k, font=(F, 8, "bold"), fg=c if c != INK else SUB, bg=CARD).pack(anchor="w", padx=8, pady=(6, 0))
            tk.Label(cell, text=o, font=(F, 8), fg=MUTED, bg=CARD).pack(anchor="w", padx=8)
            tk.Label(cell, text=f"{v:.2f}", font=(F, 15, "bold"), fg=c, bg=CARD).pack(anchor="w", padx=8, pady=(0, 6))
        if sig.get("size") == 300:
            self.wrapping(tk.Label(b, text="Use the $300 size: confirmations are weak or it's counter-trend", font=(F, 9, "bold"),
                                   fg=AMBER, bg=bgc, justify="left"), b, 30).pack(anchor="w", padx=14, pady=(0, 4))
        sizes = tk.Frame(b, bg=bgc); sizes.pack(fill="x", padx=14)
        for cap in (300, 500, 600):
            s = sig.get(f"s{cap}")
            if s is None and cap == 600:                                     # the scanner plans $300/$500 only
                sh = int(600 / sig["entry"])
                s = [sh, round(sh * (sig["entry"] - sig["stop"]) + FEE), round(sh * (sig["target"] - sig["entry"]) - FEE)]
            if s:
                tk.Label(sizes, text=f"${cap} → {s[0]} sh   risk −${s[1]}   reward +${s[2]}", font=("Consolas", 10),
                         fg=INK, bg=bgc, anchor="w").pack(fill="x")
        self.wrapping(tk.Label(b, text=sig["why"], font=(F, 9), fg=SUB, bg=bgc, justify="left"), b, 30).pack(anchor="w", padx=14, pady=(6, 2))
        self.wrapping(tk.Label(b, text="Skip if price is more than 1.5% above entry · sell by " + sig.get("exit", "14:55" if short else "15:50"),
                               font=(F, 9, "bold"), fg=col, bg=bgc, justify="left"), b, 30).pack(anchor="w", padx=14)
        btns = tk.Frame(b, bg=bgc); btns.pack(fill="x", padx=14, pady=(8, 12))
        orders = f"BUY {sig['etf']} limit {sig['entry']:.2f} | STOP-MARKET {sig['stop']:.2f} | SELL LIMIT {sig['target']:.2f}"
        self.button(btns, "Copy orders", lambda: (self.clipboard_clear(), self.clipboard_append(orders)), BLUE).pack(side="left")
        self.button(btns, "Dismiss", lambda k=self.key(sig): (self.dismissed.add(k), self.refresh(force=True)), SUB).pack(side="left", padx=8)

    @staticmethod
    def read_alerts(n=12):
        """Today's entry signals, exits and paper results from the shared alerts log, newest first."""
        try:
            with open(alerts_log(), encoding="utf-8") as f:
                lines = [l.rstrip("\n").split("\t", 1)[-1] for l in f]
        except Exception:
            return []
        keep = [l for l in lines if "ACTION" in l or "PAPER" in l]
        return keep[::-1][:n]

    def render_alerts(self, lines):
        b = self.alerts; self.clear(b)
        head = tk.Frame(b, bg=CARD); head.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(head, text="Today's alerts", font=(F, 10, "bold"), fg=INK, bg=CARD).pack(side="left")
        tk.Label(head, text="entries · exits · paper results", font=(F, 8), fg=MUTED, bg=CARD).pack(side="right")
        if not lines:
            tk.Label(b, text="None yet today.", font=(F, 9), fg=MUTED, bg=CARD).pack(anchor="w", padx=12, pady=(0, 10))
            return
        for l in lines:
            parts = [p.strip() for p in l.split("|")]
            t = parts[0].strip("[] ").split("]")[0] if parts else ""
            body = " · ".join(parts[1:4]) if len(parts) > 1 else l
            buy, paper = "BUY" in l and "ACTION" in l, "PAPER" in l
            col = BLUE if paper else GREEN if buy else RED if ("EXIT" in l or "SELL" in l or "STOP" in l) else AMBER
            row = tk.Frame(b, bg=CARD); row.pack(fill="x", padx=12, pady=1)
            tk.Label(row, text=t[:5], font=("Consolas", 9), fg=MUTED, bg=CARD).pack(side="left", anchor="n")
            self.wrapping(tk.Label(row, text=body, font=(F, 9, "bold" if buy else "normal"), fg=col, bg=CARD,
                                   anchor="w", justify="left"), row, 60).pack(side="left", fill="x", padx=(6, 0))
        tk.Frame(b, bg=CARD, height=8).pack()

    def render_position(self, p):
        self.clear(self.pos)
        if not p: return
        up = p["pnl"] >= 0; col = GREEN if up else RED
        c = tk.Frame(self.pos, bg=CARD, highlightthickness=2, highlightbackground=BLUE); c.pack(fill="x", pady=(8, 0))
        row = tk.Frame(c, bg=CARD); row.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(row, text=f"{'+' if up else '−'}${abs(p['pnl']):.2f}", font=(F, 13, "bold"), fg=col, bg=CARD).pack(side="right")
        tk.Label(row, text=f"OPEN · {p['shares']:g} {p['etf']} @ {p['entry']:.2f}", font=(F, 10, "bold"), fg=BLUE, bg=CARD).pack(side="left")
        self.wrapping(tk.Label(c, text=f"now {p['px']:.2f} · stop {p['stop']:.2f} · target {p['target']:.2f} · out by {p['exit']}",
                               font=(F, 9), fg=SUB, bg=CARD, justify="left"), c, 28).pack(anchor="w", padx=12)
        self.wrapping(tk.Label(c, text=p.get("next", ""), font=(F, 10, "bold"), fg=AMBER if p.get("urgent") else INK, bg=CARD,
                               justify="left"), c, 28).pack(anchor="w", padx=12, pady=(2, 8))

    def render_cards(self, tick):
        self.clear(self.cards); self.tiles = []
        if not tick:                     # the scanner needs 3 finished 5-min bars per ticker (about 09:45)
            c = tk.Frame(self.cards, bg=CARD, highlightthickness=1, highlightbackground=LINE)
            tk.Label(c, text="Waiting for price data", font=(F, 11, "bold"), fg=SUB, bg=CARD).pack(anchor="w", padx=14, pady=(12, 0))
            self.wrapping(tk.Label(c, text="Ticker cards appear after three 5-minute bars, about 09:45 ET.",
                                   font=(F, 9), fg=MUTED, bg=CARD, justify="left"), c, 30).pack(anchor="w", padx=14, pady=(2, 12))
            self.tiles.append(c)
        for u, d in tick.items():
            px, vw, lod = d["px"], d["vw"], d["lod"]; dist = (px / vw - 1) * 100
            above = dist >= 0; acc = GREEN if above else RED
            c = tk.Frame(self.cards, bg=CARD, highlightthickness=1, highlightbackground=LINE)
            tk.Frame(c, bg=acc, width=4).pack(side="left", fill="y")             # accent: side of VWAP
            inner = tk.Frame(c, bg=CARD); inner.pack(side="left", fill="both", expand=True)
            row = tk.Frame(inner, bg=CARD); row.pack(fill="x", padx=10, pady=(8, 0))
            tk.Label(row, text=u, font=(F, 12, "bold"), fg=INK, bg=CARD).pack(side="left")
            self.pill(row, f"{dist:+.1f}% vs VWAP", acc, GREEN_BG if above else RED_BG, bold=True).pack(side="left", padx=8)
            tk.Label(row, text=f"{px:,.2f}", font=(F, 12, "bold"), fg=INK, bg=CARD).pack(side="right")
            tk.Label(inner, text=f"VWAP {vw:,.2f}   ·   low {lod:,.2f}", font=(F, 9), fg=MUTED, bg=CARD, anchor="w").pack(fill="x", padx=10)
            self.gauge(inner, lod, vw, px)
            chips = tk.Frame(inner, bg=CARD); chips.pack(fill="x", padx=10, pady=(0, 4))
            for e, v in d["etfs"].items():
                lng = e == d.get("long")
                self.pill(chips, f"{e} {v:.2f} · {'long' if lng else 'short'}", GREEN if lng else RED,
                          GREEN_BG if lng else RED_BG).pack(side="left", padx=(0, 6))
            k = d.get("ctx") or {}
            if k.get("rsi") is not None:
                adx = (f"ADX {k['adx']:.0f} {'↓' if k.get('di') == 'down' else '↑'} "
                       f"{'trend' if k['adx'] >= 25 else 'chop' if k['adx'] < 20 else 'weak'}") if k.get("adx") is not None else "ADX n/a"
                parts = [f"RSI {k['rsi']:.0f}", adx, f"swings {k.get('trend', 'n/a')}"]
                if k.get("vol") is not None: parts.append(f"vol {k['vol']:.1f}x")
                self.wrapping(tk.Label(inner, text=" · ".join(parts) + (" · (stale)" if k.get("stale") else ""),
                                       font=(F, 8), fg=MUTED, bg=CARD, anchor="w", justify="left"), inner, 24).pack(fill="x", padx=10)
            if k.get("div"):
                dv = k["div"]; bull = dv["kind"] == "bullish"
                self.wrapping(tk.Label(inner, text=f"{'▲' if bull else '▼'} {dv['kind']} RSI divergence at {dv['at']}: "
                                       + ("selling weakening" if bull else "buying weakening"),
                                       font=(F, 9, "bold"), fg=GREEN if bull else RED, bg=GREEN_BG if bull else RED_BG,
                                       anchor="w", justify="left", padx=6), inner, 32).pack(fill="x", padx=10, pady=(3, 0))
            self.wrapping(tk.Label(inner, text=self.status(dist), font=(F, 9), fg=SUB, bg=CARD, anchor="w", justify="left"),
                          inner, 24).pack(fill="x", padx=10, pady=(2, 8))
            self.tiles.append(c)

    def gauge(self, parent, lo, vw, px):
        cv = tk.Canvas(parent, height=16, bg=CARD, highlightthickness=0); cv.pack(fill="x", padx=10, pady=4)
        def draw(e=None):
            cv.delete("all"); w = max(cv.winfo_width(), 60)
            hi = vw + (vw - lo) * 0.3; span = hi - lo or 1
            x = lambda v: 6 + (w - 12) * min(max((v - lo) / span, 0), 1)
            cv.create_line(6, 8, w - 6, 8, fill=LINE, width=5, capstyle="round")
            cv.create_line(6, 8, x(px), 8, fill=GREEN if px >= vw else RED, width=5, capstyle="round")
            cv.create_line(x(vw), 1, x(vw), 15, fill=BLUE, width=2)
            cv.create_oval(x(px) - 6, 2, x(px) + 6, 14, fill=GREEN if px >= vw else RED, outline=CARD, width=2)
        cv.bind("<Configure>", draw)

    @staticmethod
    def status(dist):
        if dist <= -2: return "Far below VWAP · wait for a bounce toward VWAP"
        if dist < 0: return "Testing VWAP from below · watch the 5-min close"
        if dist < 1: return "Just above VWAP · a reclaim needs to hold"
        return "Above VWAP · buyers in control"

    # ---------- data ----------
    @staticmethod
    def key(sig): return f"{sig['t']}|{sig['etf']}"

    @staticmethod
    def minutes(t):
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5)))
        h, m, s = map(int, t.split(":")); then = now.replace(hour=h, minute=m, second=s)
        return (now - then).total_seconds() / 60

    def age(self, t):
        m = self.minutes(t); return "just now" if m < 1 else f"{int(m)} min ago"

    @staticmethod
    def load(path, max_age=None):
        try:
            if max_age is not None and time.time() - os.path.getmtime(path) > max_age: return None
            with open(path) as f: return json.load(f)
        except Exception:
            return None

    def refresh(self, force=False):
        st = self.load(STATE)
        fresh = st is not None and time.time() - os.path.getmtime(STATE) < 90
        self.dot.configure(fg=GREEN if fresh else AMBER)
        if st:
            self.clock.configure(text=f"{st['time'][:5]} ET")
            self.feed.configure(text=f"Feed {st['feed']}" + ("" if fresh else " · stale"), fg=MUTED if fresh else AMBER)
            live = [s for s in st["signals"] if self.minutes(s["t"]) <= ACTIVE_MIN and self.key(s) not in self.dismissed]
            new = [s for s in live if self.key(s) not in self.seen]
            for s in st["signals"]: self.seen.add(self.key(s))
            if new:
                self.attributes("-topmost", True); self.deiconify(); self.lift()
                for _ in range(3): winsound.Beep(1200, 180)
            v = st.get("vix")
            if v:
                c = RED if v["state"] == "extreme" else AMBER if v["state"] == "elevated" else GREEN
                self.vix.configure(text=f"VIX {v['level']:.1f}  {v['chg']:+.1f}% · {v['off_high']:+.1f}% off high · {v['state']}", fg=c)
            pos = self.load(POSITION, max_age=180)
            sig = live[-1] if live else None
            news = (self.load(NEWS) or [])[:3]
            alerts = self.read_alerts()
            # rebuild only when something visible changed (the minute keeps the "x min ago" fresh)
            stamp = json.dumps([st["tickers"], sig, pos, news, alerts, st.get("notes"), sorted(self.dismissed), time.strftime("%H:%M")],
                               sort_keys=True, default=str)
            if force or stamp != self.sig_last:
                self.sig_last = stamp
                self.render_position(pos)
                self.render_banner(sig)
                self.render_alerts(alerts)
                n_before = len(self.tiles); self.render_cards(st["tickers"])
                if len(self.tiles) != n_before: self.layout_key = None
                self.relayout()
                self.note.configure(text=" · ".join(st.get("notes", [])))
                if news:
                    self.news_items = news; self.render_news()
        if not force:
            self.after(2000, self.refresh)


if __name__ == "__main__":
    Radar().mainloop()
