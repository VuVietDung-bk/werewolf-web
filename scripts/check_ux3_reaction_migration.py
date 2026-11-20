"""Reaction migration/uniqueness/cascade and source preservation, in its own DB."""
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
    if parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port != 55432 or parsed.path != '/ma_soi_ux2':
        raise SystemExit('Use disposable local ma_soi_ux2 at localhost:55432 only')
    name = 'ma_soi_reaction_migration_' + uuid4().hex[:10]
    admin_url = urlunsplit(parsed._replace(scheme='postgresql', path='/postgres'))
    url = urlunsplit(parsed._replace(scheme='postgresql', path='/' + name))
    env = {**os.environ, 'DATABASE_URL': url.replace('postgresql://', 'postgresql+psycopg://', 1)}
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
        try:
            def migrate(target, direction='upgrade'):
                subprocess.run([str(ROOT / '.venv/bin/alembic'), '-c', 'backend/alembic.ini', direction, target],
                    cwd=ROOT, env=env, check=True, capture_output=True)
            migrate('0031_channel_reads')
            host, player, room, source, message, channel, reaction_event = [uuid4() for _ in range(7)]
            with psycopg.connect(url) as db:
                for uid in (host, player): db.execute('INSERT INTO users(id,display_name) VALUES(%s,%s)', (uid, 'Reaction migration'))
                db.execute("INSERT INTO rooms(id,owner_user_id,code,name,seat_limit,status,version) VALUES(%s,%s,%s,'Reaction migration',2,'playing',1)", (room, host, uuid4().hex[:12]))
                db.execute("INSERT INTO room_channels(id,room_id,kind,channel_key) VALUES(%s,%s,'common','')", (channel, room))
                db.execute("INSERT INTO game_events(id,room_id,sequence,actor_user_id,kind,visibility,payload) VALUES(%s,%s,1,%s,'chat.sent','room',%s)",
                    (source, room, host, Jsonb({'channel': 'common', 'request': {'text': 'Original 🌙'}})))
                db.execute("INSERT INTO chat_messages(id,room_id,event_id,sequence,updated_sequence,channel,sender_user_id,text) VALUES(%s,%s,%s,1,1,'common',%s,'Original 🌙')", (message, room, source, host))
                db.execute('INSERT INTO room_delivery_streams(room_id,user_id,position) VALUES(%s,%s,1)', (room, player))
                db.execute('INSERT INTO channel_reads(room_id,user_id,channel_id,position) VALUES(%s,%s,%s,1)', (room, player, channel))
            migrate('0032_message_reactions')
            with psycopg.connect(url) as db:
                assert db.execute('SELECT count(*) FROM message_reactions').fetchone()[0] == 0
                assert db.execute('SELECT id,event_id,text,updated_sequence FROM chat_messages WHERE room_id=%s', (room,)).fetchone() == (message, source, 'Original 🌙', 1)
                for uid in (host, player):
                    db.execute('INSERT INTO message_reactions(source_event_id,user_id,emoji,room_id) VALUES(%s,%s,%s,%s)', (source, uid, '👨‍👩‍👧‍👦', room))
                assert db.execute('SELECT count(*) FROM message_reactions WHERE emoji=%s', ('👨‍👩‍👧‍👦',)).fetchone()[0] == 2
                try:
                    with db.transaction():
                        db.execute('INSERT INTO message_reactions(source_event_id,user_id,emoji,room_id) VALUES(%s,%s,%s,%s)', (source, player, '👨‍👩‍👧‍👦', room))
                except psycopg.errors.UniqueViolation: pass
                else: raise AssertionError('Duplicate actor/source/emoji was accepted')
                db.execute("INSERT INTO game_events(id,room_id,sequence,actor_user_id,kind,visibility,payload) VALUES(%s,%s,2,%s,'chat.reaction_changed','room',%s)",
                    (reaction_event, room, player, Jsonb({'source_event_id': str(source), 'emoji': '👨‍👩‍👧‍👦', 'present': True})))
                db.execute('UPDATE rooms SET version=2 WHERE id=%s', (room,))
            migrate('0031_channel_reads', 'downgrade')
            with psycopg.connect(url) as db:
                assert db.execute('SELECT id FROM game_events WHERE room_id=%s ORDER BY sequence', (room,)).fetchall() == [(source,), (reaction_event,)]
                assert db.execute('SELECT position FROM channel_reads WHERE room_id=%s', (room,)).fetchone()[0] == 1
                assert db.execute('SELECT count(*) FROM outbox').fetchone()[0] == 0
            migrate('0032_message_reactions')
            with psycopg.connect(url) as db:
                # Downgrade discards the current projection; do not claim it restores reactions.
                assert db.execute('SELECT count(*) FROM message_reactions').fetchone()[0] == 0
                db.execute('INSERT INTO message_reactions(source_event_id,user_id,emoji,room_id) VALUES(%s,%s,%s,%s)', (source, player, '🐺', room))
                db.execute('DELETE FROM rooms WHERE id=%s', (room,))
                assert db.execute('SELECT count(*) FROM message_reactions').fetchone()[0] == 0
            result = {'migration': '0032_message_reactions', 'result': 'pass', 'checks': [
                'upgrade preserves old source IDs/text/positions and starts an empty reaction projection',
                'full Unicode ZWJ emoji stored without truncation',
                'one reaction per source/actor/emoji enforced by database',
                'two actors may choose the same emoji',
                'downgrade preserves source event and reaction history',
                'read checkpoints and outbox remain unchanged',
                're-upgrade is empty after downgrade; current reactions are not silently reconstructed',
                'room retention cascades current reactions'], 'database': 'isolated temporary PostgreSQL14 database, deleted afterwards'}
            (ROOT / 'docs/ux-3/reaction-migration-results.json').write_text(json.dumps(result, indent=2) + '\n')
            print('Reaction migration: 8 checks passed', flush=True)
        finally:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__ == '__main__': main()
