"""Live flip detection (pure, no Streamlit).

In live mode the page recomputes the recommendation every second (price) and the
signal engine every ~45s. `diff_states` compares the previous snapshot to the
current one and emits human-readable Events for anything that *changed* — the
traffic light flipped, a swing setup turned GO/NO-GO, a new signal appeared, a
trend change crossed the threshold, or price touched the stop/target/key level.

This is what powers the toast alerts and the event feed, and it answers
"why did the decision just change?" deterministically (no AI).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LiveState:
    """A snapshot of everything we watch for changes between ticks."""
    light: str = ""                    # swing light label/key (go/forming/no)
    swing_go: bool = False
    rr: float = 0.0
    swing_setup: str = ""
    trend_change_likely: bool = False
    trend_change_dir: str = ""
    preset_key: str = ""               # investor read bucket key
    preset_label: str = ""
    signals: frozenset = field(default_factory=frozenset)
    price: float = 0.0
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    key_level: float | None = None
    kind: str = ""                     # immediate / breakout_wait / no_trade
    trigger: float | None = None       # breakout_wait: the wall to close beyond
    bull: bool = True                  # plan direction (True = long)


@dataclass(frozen=True)
class Event:
    kind: str                          # light / swing / setup / preset / trend_change /
                                       # signal_new / signal_gone / stop / target / level
    text: str
    severity: str = "info"             # good / bad / warn / info


def _crossed(level: float | None, a: float | None, b: float | None) -> bool:
    """True if `level` lies between prices a and b (price moved through it)."""
    if level is None or a is None or b is None or a == b:
        return False
    lo, hi = (a, b) if a < b else (b, a)
    return lo <= level <= hi


def diff_states(prev: LiveState | None, cur: LiveState) -> list[Event]:
    if prev is None:
        return []
    events: list[Event] = []

    # Swing GO / NO-GO flip — the headline alert.
    if cur.swing_go != prev.swing_go:
        if cur.swing_go:
            events.append(Event("swing", f"⚡ Swing turned GO — R:R {cur.rr:.1f}:1", "good"))
        else:
            events.append(Event("swing", f"Swing left GO — R:R now {cur.rr:.1f}:1", "warn"))

    # Traffic-light label change (covers forming↔no etc., not already a GO flip).
    if cur.light and cur.light != prev.light and cur.swing_go == prev.swing_go:
        events.append(Event("light", f"Signal: {prev.light} → {cur.light}", "info"))

    # Swing setup name change.
    if cur.swing_setup and cur.swing_setup != prev.swing_setup:
        events.append(Event("setup", f"Setup: {prev.swing_setup or '—'} → {cur.swing_setup}",
                            "info"))

    # Investor (long-term) read bucket change.
    if cur.preset_key and cur.preset_key != prev.preset_key:
        events.append(Event("preset", f"Long-term read: {prev.preset_label or '—'} → "
                            f"{cur.preset_label}", "info"))

    # Trend change crossing into 'likely', or flipping direction.
    if cur.trend_change_likely and (not prev.trend_change_likely
                                    or cur.trend_change_dir != prev.trend_change_dir):
        events.append(Event("trend_change",
                            f"⚠️ Possible trend change toward {cur.trend_change_dir}", "warn"))

    # New / cleared engine signals.
    for name in sorted(cur.signals - prev.signals):
        events.append(Event("signal_new", f"New signal: {name}", "info"))
    for name in sorted(prev.signals - cur.signals):
        events.append(Event("signal_gone", f"Signal cleared: {name}", "info"))

    # Plan kind changed (immediate ↔ breakout_wait ↔ no_trade).
    if cur.kind and prev.kind and cur.kind != prev.kind:
        nice = {"immediate": "tradable now", "breakout_wait": "wait for the breakout",
                "no_trade": "no trade"}
        events.append(Event("kind", f"Plan changed: {nice.get(prev.kind, prev.kind)} → "
                            f"{nice.get(cur.kind, cur.kind)}", "warn"))

    # 🚀 Breakout trigger hit — price crossed the armed wall in the plan's direction.
    if cur.trigger is not None and prev.price and cur.price:
        fired = (prev.price < cur.trigger <= cur.price if cur.bull
                 else prev.price > cur.trigger >= cur.price)
        if fired:
            events.append(Event("trigger",
                                f"🚀 Breakout trigger hit — price crossed ${cur.trigger:.2f}",
                                "good"))

    # Price touching the plan levels.
    if _crossed(cur.stop, prev.price, cur.price):
        events.append(Event("stop", f"Price hit the stop ${cur.stop:.2f}", "bad"))
    if _crossed(cur.target, prev.price, cur.price):
        events.append(Event("target", f"Price reached the target ${cur.target:.2f}", "good"))
    if _crossed(cur.key_level, prev.price, cur.price):
        events.append(Event("level", f"Price crossed the key level ${cur.key_level:.2f}", "info"))

    return events
