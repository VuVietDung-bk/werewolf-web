#!/usr/bin/env bash
set -euo pipefail
umask 077

# PG_CLIENT=compose uses the PostgreSQL client shipped with the local container.
# PG_CLIENT=host uses standard PGHOST/PGPORT/PGUSER/PGPASSWORD connection settings.
PG_CLIENT=${PG_CLIENT:-compose}
PGDATABASE=${PGDATABASE:-ma_soi}
PGUSER=${PGUSER:-ma_soi}
BACKUP_DIR=${BACKUP_DIR:-backups}

pg_run() {
  if [[ $PG_CLIENT == compose ]]; then
    docker compose exec -T postgres "$@"
  elif [[ $PG_CLIENT == host ]]; then
    "$@"
  else
    echo 'PG_CLIENT must be compose or host' >&2
    exit 2
  fi
}

usage() {
  echo 'Usage: scripts/postgres_maintenance.sh backup | restore-check ARCHIVE TARGET_DB | backup-prune-preview DAYS | backup-prune DAYS | retention-preview DAYS | retention-delete DAYS | daily' >&2
  exit 2
}

case ${1:-} in
  daily)
    [[ $# == 1 && ${RETENTION_POLICY_APPROVED:-} == 1 ]] || { echo 'Daily maintenance requires RETENTION_POLICY_APPROVED=1.' >&2; exit 2; }
    "$0" backup
    "$0" retention-delete 90
    ;;
  backup)
    [[ $# == 1 ]] || usage
    mkdir -p -- "$BACKUP_DIR"
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    archive="$BACKUP_DIR/$PGDATABASE-$stamp.dump"
    [[ ! -e $archive && ! -e $archive.sha256 ]] || { echo 'Backup name already exists' >&2; exit 1; }
    partial=$(mktemp "$BACKUP_DIR/.backup-XXXXXXXX")
    trap 'rm -f -- "$partial"' EXIT
    start=$SECONDS
    pg_run pg_dump -U "$PGUSER" -d "$PGDATABASE" --format=custom --no-owner --no-acl > "$partial"
    pg_run pg_restore --list < "$partial" > /dev/null
    mv -- "$partial" "$archive"
    (cd -- "$BACKUP_DIR" && sha256sum -- "$(basename -- "$archive")") > "$archive.sha256"
    echo "Backup: $archive ($(wc -c < "$archive") bytes, ${SECONDS-start}s)"
    echo "SHA-256: $archive.sha256"
    ;;
  restore-check)
    [[ $# == 3 ]] || usage
    archive=$2
    target=$3
    [[ -f $archive && -f $archive.sha256 ]] || { echo 'Archive and checksum required' >&2; exit 1; }
    [[ $target =~ ^restore_[a-z0-9_]{1,48}$ && $target != "$PGDATABASE" ]] || { echo 'Target must be a new restore_* database' >&2; exit 2; }
    read -r expected_hash expected_name < "$archive.sha256"
    [[ $expected_hash =~ ^[0-9a-f]{64}$ && $expected_name == "$(basename -- "$archive")" ]] || { echo 'Checksum does not name this archive' >&2; exit 1; }
    actual_hash=$(sha256sum -- "$archive")
    [[ ${actual_hash%% *} == "$expected_hash" ]] || { echo 'Archive SHA-256 mismatch' >&2; exit 1; }
    echo "$(basename -- "$archive"): OK"
    pg_run pg_restore --list < "$archive" > /dev/null
    existing=$(pg_run psql -X -U "$PGUSER" -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname = '$target'")
    [[ -z $existing ]] || { echo 'Target database already exists; refusing to overwrite' >&2; exit 1; }
    start=$SECONDS
    pg_run createdb -U "$PGUSER" -- "$target"
    # Keep a failed restore for inspection; never fall back to the source database.
    pg_run pg_restore -U "$PGUSER" -d "$target" --exit-on-error --no-owner --no-acl < "$archive"
    pg_run psql -X -U "$PGUSER" -d "$target" -Atc "SELECT 'alembic=' || version_num FROM alembic_version; SELECT 'rooms=' || count(*) FROM rooms; SELECT 'members=' || count(*) FROM room_members; SELECT 'events=' || count(*) FROM game_events; SELECT 'outbox=' || count(*) FROM outbox;"
    echo "Restored into $target in ${SECONDS-start}s; keep this database for inspection or drop it manually."
    ;;
  backup-prune-preview|backup-prune)
    [[ $# == 2 && $2 == 7 ]] || { echo 'The approved backup retention period is 7 days.' >&2; exit 2; }
    if [[ $1 == backup-prune && ${RETENTION_POLICY_APPROVED:-} != 1 ]]; then
      echo 'Set RETENTION_POLICY_APPROVED=1 to remove expired backups.' >&2
      exit 2
    fi
    # Daily pruning starts after day 6 so a backup cannot age past day 7
    # between scheduled runs when the job is operating normally.
    cutoff_epoch=$(($(date -u +%s) - 6 * 86400))
    count=0
    for archive in "$BACKUP_DIR"/"$PGDATABASE"-*.dump; do
      [[ -f $archive && ! -L $archive ]] || continue
      filename=${archive##*/}
      stamp=${filename#"$PGDATABASE"-}
      [[ $stamp =~ ^[0-9]{8}T[0-9]{6}Z[.]dump$ ]] || continue
      stamp=${stamp%.dump}
      archive_epoch=$(date -u -d "${stamp:0:4}-${stamp:4:2}-${stamp:6:2}T${stamp:9:2}:${stamp:11:2}:${stamp:13:2}Z" +%s) || { echo "Invalid backup date: $filename" >&2; exit 1; }
      if (( archive_epoch <= cutoff_epoch )); then
        if [[ $1 == backup-prune ]]; then
          rm -f -- "$archive" "$archive.sha256"
        fi
        (( count += 1 ))
      fi
    done
    echo "${1}: ${count} backup(s) ready for the 7-day retention limit"
    ;;
  retention-preview|retention-delete)
    [[ $# == 2 && $2 =~ ^[1-9][0-9]{0,4}$ ]] || usage
    days=$2
    cutoff="now() - interval '$((days * 24)) hours'"
    if [[ $1 == retention-delete ]]; then
      [[ $days == 90 ]] || { echo 'The approved room retention period is 90 days.' >&2; exit 2; }
      [[ ${RETENTION_POLICY_APPROVED:-} == 1 ]] || { echo 'Set RETENTION_POLICY_APPROVED=1 only after the public retention policy is finalized.' >&2; exit 2; }
      pg_run psql -X -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$PGDATABASE" -Atc "WITH victims AS (SELECT id FROM rooms WHERE status = 'finished' AND ended_at < $cutoff ORDER BY ended_at, id LIMIT 100 FOR UPDATE SKIP LOCKED), removed AS (DELETE FROM rooms WHERE id IN (SELECT id FROM victims) RETURNING id) SELECT count(*) FROM removed;"
    else
      pg_run psql -X -v ON_ERROR_STOP=1 -U "$PGUSER" -d "$PGDATABASE" -Atc "SELECT count(*) AS eligible_rooms, coalesce(sum((SELECT count(*) FROM game_events e WHERE e.room_id = r.id)), 0) AS affected_events FROM rooms r WHERE status = 'finished' AND ended_at < $cutoff;"
    fi
    ;;
  *) usage ;;
esac
