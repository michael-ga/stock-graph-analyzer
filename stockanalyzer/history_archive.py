"""Portable history archives and offline team reports. Standard library only."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import datetime, timezone
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

FORMAT = "stockanalyzer-history"
TEAM_FORMAT = "stockanalyzer-team-history"
SCHEMA_VERSION = 1
TABLES = ("analysis_runs", "analysis_timeframes", "trades", "trade_events",
          "trade_signals", "trade_indicators", "trade_verdict", "trade_swing_checks", "paper_trades")
MAX_JSON_BYTES = 128 * 1024 * 1024


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def csv_bytes(rows: list[dict]) -> bytes:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        rendered = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                value = "'" + value
            rendered[key] = value
        writer.writerow(rendered)
    return output.getvalue().encode("utf-8-sig")


def make_archive(document: dict) -> bytes:
    """JSON is lossless; accompanying CSVs are convenient spreadsheet summaries."""
    output = io.BytesIO()
    is_team = document["format"] == TEAM_FORMAT
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("history.json", json.dumps(document, ensure_ascii=False, allow_nan=False))
        metadata = {k: v for k, v in document.items() if k not in ("tables", "records")}
        archive.writestr("manifest.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        for table in ("trades", "paper_trades", "analysis_runs"):
            rows = ([{**item["source"], **item["record"]} for item in document["records"]
                     if item["table"] == table] if is_team else document["tables"].get(table, []))
            archive.writestr(f"{table}.csv", csv_bytes(rows))
        if is_team:
            archive.writestr("ideas.csv", csv_bytes(document["report"]["ideas"]))
    return output.getvalue()


def read_archive(data: bytes) -> dict:
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("history.json")
            if info.file_size > MAX_JSON_BYTES:
                raise ValueError("History JSON exceeds the 128 MB archive limit")
            document = json.loads(archive.read(info))
    except (BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Not a valid Stock Analyzer history archive") from exc
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported history schema version")
    if document.get("format") not in (FORMAT, TEAM_FORMAT):
        raise ValueError("Unsupported history archive format")
    return document


def _records(document: dict):
    if document["format"] == TEAM_FORMAT:
        yield from document["records"]
        return
    participant = document["participant"]
    source = {
        "installation_id": document["installation_id"], "participant_id": participant["id"],
        "participant": participant["label"], "exported_at": document["exported_at"],
    }
    for table, rows in document["tables"].items():
        if table not in TABLES:
            raise ValueError(f"Unsupported history table: {table}")
        for row in rows:
            yield {"table": table, "source": source, "record": row}


def _record_key(item: dict) -> tuple:
    source, record, table = item["source"], item["record"], item["table"]
    if table not in TABLES:
        raise ValueError(f"Unsupported history table: {table}")
    record_id = record.get("id", record.get("trade_id"))
    if record_id is None:
        raise ValueError("History record has no stable identifier")
    return source["installation_id"], source["participant_id"], table, str(record_id)


def _version(row: dict) -> str:
    return row.get("app_version") or (row.get("snapshot") or {}).get("app_version") or "unknown"


def _idea_key(row: dict) -> str:
    snapshot = row.get("snapshot") or {}
    day = datetime.fromtimestamp(float(row["opened_ts"]), timezone.utc).date().isoformat()
    identity = [_version(row), row.get("ticker"), snapshot.get("setup"), row.get("kind"),
                *(round(float(row.get(field) or 0), 2) for field in ("entry", "init_stop", "target")), day]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]


def team_report(records: list[dict]) -> dict:
    cohorts = defaultdict(list)
    groups = defaultdict(list)
    for item in records:
        row = item["record"]
        if item["table"] != "trades" or row.get("status") != "closed" or row.get("close_reason") == "cancelled":
            continue
        cohorts[_idea_key(row)].append(item)
        source = item["source"]
        groups[(source["installation_id"], source["participant_id"], source["participant"],
                row.get("trader", ""), _version(row))].append(row)
    ideas = []
    for identity, items in sorted(cohorts.items()):
        rows = [item["record"] for item in items]
        pnls = [float(row.get("pnl_pct") or 0) for row in rows]
        ideas.append({
            "idea_id": identity, "ticker": rows[0]["ticker"], "app_version": _version(rows[0]),
            "observations": len(rows), "mean_pnl_pct": sum(pnls) / len(pnls),
            "min_pnl_pct": min(pnls), "max_pnl_pct": max(pnls),
            "participants": sorted({item["source"]["participant"] for item in items}),
        })
    by_trader = [{
        "participant": participant, "trader": trader, "app_version": version,
        "installation_id": installation, "participant_id": participant_id,
        "closed_trades": len(rows),
        "win_rate": sum(float(row.get("pnl_pct") or 0) > 0 for row in rows) / len(rows) * 100,
        "mean_pnl_pct": sum(float(row.get("pnl_pct") or 0) for row in rows) / len(rows),
    } for (installation, participant_id, participant, trader, version), rows in sorted(groups.items())]
    return {"unique_market_ideas": len(ideas), "trade_observations": sum(len(v) for v in cohorts.values()),
            "ideas": ideas, "by_participant_and_trader": by_trader,
            "notes": "Identical stock/setup/levels/day/version count as one market idea. Outcomes remain separate observations; unknown versions cannot establish algorithm equivalence."}


def combine_archives(archives: list[bytes]) -> dict:
    """Newest export wins for a repeated record, including open -> closed updates."""
    combined = {}
    for data in archives:
        document = read_archive(data)
        try:
            for item in _records(document):
                key = _record_key(item)
                stamp = datetime.fromisoformat(item["source"]["exported_at"])
                if stamp.tzinfo is None:
                    raise ValueError("Export time must include a timezone")
                previous = combined.get(key)
                if previous is None or stamp > previous[0]:
                    combined[key] = stamp, item
                elif stamp == previous[0] and item["record"] != previous[1]["record"]:
                    raise ValueError("Conflicting records with the same export time")
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("History archive has an invalid record structure") from exc
    records = [combined[key][1] for key in sorted(combined)]
    return {"format": TEAM_FORMAT, "schema_version": SCHEMA_VERSION, "exported_at": utc_now(),
            "records": records, "report": team_report(records)}
