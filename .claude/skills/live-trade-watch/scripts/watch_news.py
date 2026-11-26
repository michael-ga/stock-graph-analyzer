"""News watcher: Google News RSS (no key). Prints only NEW headlines; by default only market-moving ones.

    python watch_news.py --q "chip stocks" --q "SanDisk stock" --q "AI regulation Congress" [--all]

Lines are tagged WATCH (likely market-moving keyword) or INFO (everything else, only with --all).
"""
import argparse, os, re, sys, time, datetime as dt, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
sys.path.insert(0, os.path.dirname(__file__))
from common import now, emit, WATCH, INFO, ET as ETZ, STATE_DIR

HOT = re.compile(r"regulat|ban\b|export|tariff|halt|guidance|downgrad|upgrad|probe|investigat|executive order|white house|"
                 r"congress|senate|bill\b|antitrust|rebound|bounce|recover|rally|bottom|capitulat|fed\b|powell|circuit breaker|"
                 r"margin call|liquidat|pause|walk back|clarif|statement|reassur|acquir|merger|offering|split|recall", re.I)
ap = argparse.ArgumentParser()
ap.add_argument("--q", action="append", required=True)
ap.add_argument("--all", action="store_true", help="also print routine headlines")
ap.add_argument("--every", type=int, default=120)
a = ap.parse_args()
seen, first, HEAD = set(), True, []


def fetch(q):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q + " when:1d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
    raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read()
    for it in ET.fromstring(raw).findall(".//item"):
        try:
            yield parsedate_to_datetime(it.findtext("pubDate")).astimezone(ETZ), (it.findtext("title") or "").strip()
        except Exception:
            continue


emit(INFO, "news watcher started", "queries: " + ", ".join(a.q), "hot only" if not a.all else "all headlines")
while True:
    new = []
    for q in a.q:
        try:
            for ts, title in fetch(q):
                key = re.sub(r"\W+", "", title.lower())[:80]
                if key in seen: continue
                seen.add(key)
                if now() - ts <= dt.timedelta(minutes=20 if first else 60):
                    new.append((ts, title))
        except Exception as e:
            emit(INFO, f"news fetch error ({q}): {e}")
        time.sleep(1)
    for ts, title in sorted(new, reverse=True)[: (3 if first else 8)]:
        if HOT.search(title):
            emit(WATCH, f"NEWS {ts:%H:%M}", title)
            HEAD.insert(0, dict(t=f"{ts:%H:%M}", title=title)); del HEAD[8:]
            try:   # the Trade Radar window shows these
                import json; os.makedirs(STATE_DIR, exist_ok=True); json.dump(HEAD, open(os.path.join(STATE_DIR, "news.json"), "w"))
            except Exception:
                pass
        elif a.all:
            emit(INFO, f"news {ts:%H:%M}", title)
    first = False
    if now().time() > dt.time(16, 5):
        emit(INFO, "market closed, news watcher exiting"); break
    time.sleep(a.every)
