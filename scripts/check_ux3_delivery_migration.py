"""Migration 0030 with historical audiences, in a separate disposable database."""
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]


def main():
    parsed = urlsplit(os.environ.get('DATABASE_URL', ''))
    if parsed.hostname not in ('localhost', '127.0.0.1') or parsed.port != 55432 or parsed.path != '/ma_soi_ux2':
        raise SystemExit('Use only disposable local ma_soi_ux2 at port 55432')
    name = 'ma_soi_delivery_migration_' + uuid4().hex[:10]
    admin_url = urlunsplit(parsed._replace(scheme='postgresql', path='/postgres'))
    test_url = urlunsplit(parsed._replace(scheme='postgresql', path='/' + name))
    env = {**os.environ, 'DATABASE_URL': test_url.replace('postgresql://', 'postgresql+psycopg://', 1)}
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
        try:
            def migrate(target, direction='upgrade'):
                subprocess.run([str(ROOT / '.venv/bin/alembic'), '-c', 'backend/alembic.ini', direction, target],
                    cwd=ROOT, env=env, check=True, capture_output=True)

            migrate('0029_room_channels')
            host, a, b, late, former, room = [uuid4() for _ in range(6)]
            channel_ids = {key: uuid4() for key in [('common', ''), ('vote', ''), ('announcement', ''),
                ('team', 'Old wolves'), ('team', 'New team'),
                *(('moderator_dm', str(uid)) for uid in (a, b, late, former))]}
            events = [
                ('chat.sent', 'room', {'channel': 'common'}, {a, b, former}),
                ('chat.sent', 'host', {'channel': 'team', 'team_label': 'Old wolves', 'recipient_ids': [str(a), str(former)]}, {a, former}),
                ('player.private_message_sent', 'host', {'recipient_user_id': str(a)}, {a}),
                ('player.note', 'host', {'request': {'user_id': str(a)}}, set()),
                ('future.private', 'host', {'request': {'user_id': str(a)}}, set()),
                ('vote.ballot_submitted', 'host', {}, {b}),
                ('role.request_submitted', 'host', {'request': {'user_id': str(a)}}, {a}),
                ('chat.revised', 'host', {'channel': 'team', 'team_label': 'Old wolves', 'recipient_ids': [str(a), str(former)]}, {a, former}),
                ('chat.revised', 'host', {'channel': 'host', 'participant_user_id': str(a)}, {a}),
                ('chat.sent', 'room', {'channel': 'common'}, {a, b, late}),
                ('chat.sent', 'host', {'channel': 'team', 'team_label': 'Old wolves', 'recipient_ids': [str(a), str(former)]}, {a}),
                ('chat.sent', 'host', {'channel': 'team', 'team_label': 'New team', 'recipient_ids': [str(b), str(late)]}, {b, late}),
                ('chat.sent', 'host', {'channel': 'team', 'team_label': 'Old wolves'}, set()),
                ('player.private_message_sent', 'host', {'recipient_user_id': str(former)}, set()),
                ('vote.opened', 'room', {}, {a, b, late}),
                ('roles.distributed', 'host', {'assignments': [{'user_id': str(uid)} for uid in (a, b, late)]}, {a, b, late}),
                ('roles.draft_offered', 'host', {'user_id': str(late)}, {late}),
                ('player.change_team', 'host', {'request': {'user_id': str(b)}}, {b}),
                ('chat.mute_changed', 'host', {'request': {'user_id': str(a)}}, {a}),
                ('player.private_message_sent', 'host', {'recipient_user_id': str(a)}, {a}),
                ('content.notice_edited', 'room', {}, {a, b, late}),
                ('game.phase_advanced', 'room', {}, {a, b, late}),
                ('chat.sent', 'host', {'channel': 'host', 'participant_user_id': str(b)}, {b}),
                ('role.request_reviewed', 'host', {'request': {'user_id': str(a)}}, {a}),
                ('roles.draft_started', 'host', {}, {a, b, late}),
            ]
            source_ids = [uuid4() for _ in events]
            with psycopg.connect(test_url) as db:
                for uid in (host, a, b, late, former):
                    db.execute('INSERT INTO users(id,display_name) VALUES(%s,%s)', (uid, 'Migration delivery'))
                db.execute("INSERT INTO rooms(id,owner_user_id,code,name,seat_limit,status,version) VALUES(%s,%s,%s,'Historical delivery',4,'playing',%s)",
                    (room, host, uuid4().hex[:12], len(events)))
                for seat, uid in enumerate((a, b, late, former), start=1):
                    db.execute("INSERT INTO room_members(room_id,user_id,seat_number,consent_version,consented_at,joined_at,left_at) VALUES(%s,%s,%s,'postgame-full-v1','2026-01-01',%s,%s)",
                        (room, uid, seat if uid != former else None, '2026-01-05' if uid == late else '2026-01-01', '2026-01-04' if uid == former else None))
                for (kind, key), cid in channel_ids.items():
                    db.execute('INSERT INTO room_channels(id,room_id,kind,channel_key) VALUES(%s,%s,%s,%s)', (cid, room, kind, key))
                for index, (kind, visibility, payload, _) in enumerate(events, start=1):
                    db.execute('INSERT INTO game_events(id,room_id,sequence,actor_user_id,kind,visibility,payload,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                        (source_ids[index - 1], room, index, b if kind == 'vote.ballot_submitted' else host,
                         kind, visibility, Jsonb(payload), '2026-01-02' if index < 10 else '2026-01-06'))
            for round_index in range(2):
                migrate('0030_room_deliveries')
                with psycopg.connect(test_url) as db:
                    for uid in (host, a, b, late, former):
                        expected = [index for index, event in enumerate(events, start=1) if uid == host or uid in event[3]]
                        rows = db.execute('SELECT d.position,e.sequence,d.scopes,d.channel_ids FROM room_deliveries d JOIN game_events e ON e.id=d.source_event_id WHERE d.room_id=%s AND d.user_id=%s ORDER BY d.position', (room, uid)).fetchall()
                        assert [row[0] for row in rows] == list(range(1, len(expected) + 1)), (uid, rows)
                        assert [row[1] for row in rows] == expected, (uid, expected, rows)
                        assert db.execute('SELECT position FROM room_delivery_streams WHERE room_id=%s AND user_id=%s', (room, uid)).fetchone()[0] == len(expected)
                        for _, seq, scopes, channels in rows:
                            kind, visibility, payload, _ = events[seq - 1]
                            from app.delivery import invalidation
                            expected_scopes, identity = invalidation(kind, visibility, payload)
                            assert scopes == expected_scopes, (seq, scopes, expected_scopes)
                            expected_channel = channel_ids.get(identity) if identity else None
                            assert channels == ([str(expected_channel)] if expected_channel else []), (seq, channels)
                    assert db.execute('SELECT id FROM game_events WHERE room_id=%s ORDER BY sequence', (room,)).fetchall() == [(uid,) for uid in source_ids]
                    assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0
                    # The immutable source+recipient identity cannot be inserted twice.
                    try:
                        with db.transaction():
                            db.execute('INSERT INTO room_deliveries(id,room_id,user_id,position,source_event_id,scopes,channel_ids) VALUES(%s,%s,%s,999,%s,%s,%s)',
                                (uuid4(), room, host, source_ids[0], Jsonb([]), Jsonb([])))
                    except psycopg.errors.UniqueViolation:
                        pass
                    else:
                        raise AssertionError('Duplicate source recipient accepted')
                if round_index == 0:
                    migrate('0029_room_channels', 'downgrade')
            with psycopg.connect(test_url) as db:
                db.execute('DELETE FROM rooms WHERE id=%s', (room,))
                assert db.execute('SELECT count(*) FROM room_delivery_streams').fetchone()[0] == 0
                assert db.execute('SELECT count(*) FROM room_deliveries').fetchone()[0] == 0
            output = {'migration': '0030_room_deliveries', 'result': 'pass', 'checks': [
                '25 historical events preserve provable latest membership intervals',
                'team audiences use saved recipients; absent metadata fails closed',
                'DM/request/self actions/secret ballot remain recipient-scoped; unknown private events host-only',
                'positions contiguous per recipient and source identity unique',
                'scopes/channel UUIDs agree with current write contract',
                'source events unchanged and no outbox/new announcement created',
                'downgrade and re-upgrade keep source data and audience',
                'room deletion cascades delivery streams and rows'],
                'database': 'separate temporary PostgreSQL14 database, deleted afterwards'}
            path = ROOT / 'docs/ux-3/delivery-migration-results.json'
            path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
            print('UX3 delivery migration: 8 checks passed', flush=True)
        finally:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__ == '__main__':
    main()
