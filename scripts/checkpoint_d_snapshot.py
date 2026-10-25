"""Write stable row counts and digests for checkpoint D backup comparison."""

import argparse
import hashlib
import json
import os
from pathlib import Path

import psycopg
from psycopg import sql


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    url = os.environ.get("DATABASE_URL", "")
    database = url.split("/")[-1].split("?")[0]
    if database != "ma_soi_checkpoint_d" and not database.startswith("restore_checkpoint_d_"):
        raise SystemExit("Use only ma_soi_checkpoint_d or restore_checkpoint_d_* databases")
    result = {}
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1)) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public' ORDER BY tablename")
            tables = [row[0] for row in cursor.fetchall()]
            for table in tables:
                cursor.execute(sql.SQL("SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text").format(
                    sql.Identifier(table)))
                digest = hashlib.sha256()
                count = 0
                for (row,) in cursor:
                    digest.update(json.dumps(row, sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8"))
                    digest.update(b"\n")
                    count += 1
                result[table] = {"rows": count, "sha256": digest.hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(f"{database}: {len(tables)} tables, "
          f"{result['rooms']['rows']} rooms, {result['room_members']['rows']} members, "
          f"{result['game_events']['rows']} events; {args.output}")


if __name__ == "__main__":
    main()
