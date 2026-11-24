"""Tally the live-session ledgers.

    python algolab/live/summarize.py

Per rule: validated / challenged / inconclusive across distinct session dates.
Per behavior tag: count, distinct dates and the total measured cost.
"""
import csv
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent


def _rows(name):
    with open(HERE / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def rules():
    tally = defaultdict(lambda: {"validated": set(), "challenged": set(), "inconclusive": set()})
    for r in _rows("murphy_ledger.csv"):
        tally[r["rule_id"]][r["verdict"]].add(r["date"])
    print(f"{'rule_id':34} {'valid':>5} {'chall':>5} {'incon':>5}  (distinct dates)")
    for rule_id, v in sorted(tally.items(), key=lambda kv: -len(kv[1]["validated"])):
        print(f"{rule_id:34} {len(v['validated']):5} {len(v['challenged']):5} {len(v['inconclusive']):5}")


def behavior():
    tally = defaultdict(lambda: {"kind": "", "n": 0, "dates": set(), "cost": 0.0})
    for r in _rows("behavior_ledger.csv"):
        t = tally[r["tag"]]
        t["kind"], t["n"] = r["kind"], t["n"] + 1
        t["dates"].add(r["date"])
        if r["cost_usd"]:
            t["cost"] += float(r["cost_usd"])
    print(f"\n{'tag':28} {'kind':8} {'n':>3} {'dates':>5} {'cost $':>8}")
    for tag, t in sorted(tally.items(), key=lambda kv: kv[1]["cost"]):
        print(f"{tag:28} {t['kind']:8} {t['n']:3} {len(t['dates']):5} {t['cost']:8.2f}")


if __name__ == "__main__":
    rules()
    behavior()
