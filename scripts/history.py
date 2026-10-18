"""Export a user's history or combine archives locally, without a shared server."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from stockanalyzer.history_archive import combine_archives, make_archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Export all durable history for one user")
    export.add_argument("--username", required=True)
    export.add_argument("--label")
    export.add_argument("--output", type=Path, required=True)
    merge = commands.add_parser("merge", help="Combine individual or previously combined ZIP archives")
    merge.add_argument("archives", type=Path, nargs="+")
    merge.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new filename")
    try:
        if args.command == "export":
            from dotenv import load_dotenv
            from sqlalchemy import select
            from stockanalyzer.config import Settings
            from stockanalyzer.db.models import User
            from stockanalyzer.db.session import configure_engine, session_scope
            from stockanalyzer.history_export import collect_history
            load_dotenv()
            configure_engine(Settings.from_env().database_url)
            with session_scope() as session:
                user_id = session.scalar(select(User.id).where(User.username == args.username.strip().lower()))
            if not user_id:
                parser.error("User not found")
            document = collect_history(user_id, label=args.label)
        else:
            document = combine_archives([path.read_bytes() for path in args.archives])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as output:
            output.write(make_archive(document))
        args.output.chmod(0o600)
        if args.command == "merge":
            print(json.dumps({k: v for k, v in document["report"].items() if k != "ideas"}, indent=2))
        print(f"Saved {args.output}")
        return 0
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
