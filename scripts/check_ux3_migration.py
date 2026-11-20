"""Exercise UX3 migration with old data in its own temporary local database."""
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

ROOT=Path(__file__).resolve().parents[1]


def main():
    parsed=urlsplit(os.environ.get('DATABASE_URL',''))
    if parsed.hostname not in ('127.0.0.1','localhost') or parsed.port!=55432 or parsed.path!='/ma_soi_ux2':
        raise SystemExit('Use only the disposable local ma_soi_ux2 database on port 55432')
    name='ma_soi_ux3_migration_'+uuid4().hex[:10]
    admin_url=urlunsplit(parsed._replace(scheme='postgresql',path='/postgres'))
    test_url=urlunsplit(parsed._replace(scheme='postgresql',path='/'+name))
    env={**os.environ,'DATABASE_URL':test_url.replace('postgresql://','postgresql+psycopg://',1)}
    with psycopg.connect(admin_url,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:
            def migrate(target, direction='upgrade'):
                subprocess.run([str(ROOT/'.venv/bin/alembic'),'-c','backend/alembic.ini',direction,target],
                    cwd=ROOT,env=env,check=True,capture_output=True)
            migrate('0027_vote_countdown')
            host,player,room,one,two,message_a,message_b=[uuid4() for _ in range(7)]
            with psycopg.connect(test_url) as db:
                for user_id in (host,player):db.execute('INSERT INTO users(id,display_name) VALUES(%s,%s)',(user_id,'Migration UX3'))
                db.execute("INSERT INTO rooms(id,owner_user_id,code,name,seat_limit,status,version) VALUES(%s,%s,%s,'Migration fixture',2,'playing',4)",(room,host,uuid4().hex[:12]))
                db.execute('INSERT INTO room_members(room_id,user_id,seat_number,consent_version,consented_at) VALUES(%s,%s,1,1,now())',(room,player))
                db.execute("INSERT INTO role_snapshots(room_id,set_name,definitions,distribution_mode) VALUES(%s,'Fixture','[]'::jsonb,'random')",(room,))
                db.execute('INSERT INTO role_assignments(room_id,user_id,role_id) VALUES(%s,%s,%s)',(room,player,uuid4()))
                db.execute("INSERT INTO player_states(room_id,user_id,team_chat_override_set,team_chat_label_override) VALUES(%s,%s,true,'Team override')",(room,player))
                db.execute("UPDATE role_snapshots SET definitions=%s WHERE room_id=%s",(Jsonb([{'team_chat_label':'Team snapshot'},{'team_chat_label':'Team template'}]),room))
                db.execute('INSERT INTO role_sets(room_id) VALUES(%s)',(room,))
                db.execute("INSERT INTO role_definitions(id,room_id,name,description,faction_label,team_chat_label,role_kind,request_phases,host_contact) VALUES(%s,%s,'Fixture role','Fixture','Village','Team template','passive','[]','{}')",(uuid4(),room))
                chat_event=uuid4()
                db.execute("INSERT INTO game_events(id,room_id,sequence,actor_user_id,kind,visibility,payload) VALUES(%s,%s,5,%s,'chat.sent','host','{}')",(chat_event,room,host))
                db.execute("INSERT INTO chat_messages(id,room_id,event_id,sequence,updated_sequence,channel,team_label,sender_user_id,recipient_ids,text) VALUES(%s,%s,%s,5,5,'team','Team history',%s,'[]','Old team message')",(uuid4(),room,chat_event,host))
                db.execute('UPDATE rooms SET version=5 WHERE id=%s',(room,))
                for seq,event_id,message_id in ((1,one,message_a),(2,two,message_b)):
                    db.execute("INSERT INTO game_events(id,room_id,sequence,actor_user_id,kind,visibility,payload) VALUES(%s,%s,%s,%s,'player.private_message_sent','host',%s)",
                        (event_id,room,seq,host,Jsonb({'text':'Original','recipient_user_id':str(player)})))
                    db.execute('INSERT INTO private_messages(id,room_id,user_id,sender_user_id,event_id,text) VALUES(%s,%s,%s,%s,%s,%s)',(message_id,room,player,host,event_id,'Original'))
                for seq,action in ((3,'edit'),(4,'delete')):
                    db.execute("INSERT INTO game_events(id,room_id,sequence,actor_user_id,kind,visibility,payload) VALUES(%s,%s,%s,%s,'content.revision_decided','host',%s)",
                        (uuid4(),room,seq,host,Jsonb({'request':{'source_event_id':str(one),'action':action},'new_text':'Edited' if action=='edit' else None})))
                db.execute("INSERT INTO content_revisions(source_event_id,room_id,current_text,edited_at,deleted_at) VALUES(%s,%s,'Edited',now(),now())",(one,room))
            for i in range(2):
                migrate('head')
                with psycopg.connect(test_url) as db:
                    rows=db.execute('SELECT sequence,updated_sequence,text,event_id FROM private_messages WHERE room_id=%s ORDER BY sequence',(room,)).fetchall()
                    assert rows==[(1,4,'Original',one),(2,2,'Original',two)],rows
                    assert db.execute('SELECT count(*) FROM content_revisions WHERE source_event_id=%s AND deleted_at IS NOT NULL',(one,)).fetchone()[0]==1
                    keys=set(db.execute('SELECT kind,channel_key FROM room_channels WHERE room_id=%s',(room,)).fetchall())
                    assert keys=={('common',''),('announcement',''),('vote',''),('moderator_dm',str(player)),
                        ('team','Team template'),('team','Team snapshot'),('team','Team override'),('team','Team history')},keys
                    assert db.execute('SELECT count(DISTINCT id) FROM room_channels WHERE room_id=%s',(room,)).fetchone()[0]==8
                    assert db.execute('SELECT count(*) FROM game_events WHERE room_id=%s',(room,)).fetchone()[0]==5
                    assert db.execute("SELECT count(*) FROM pg_indexes WHERE indexname IN ('ix_private_messages_room_user_sequence','ix_private_messages_room_updated')").fetchone()[0]==2
                if i==0:migrate('0027_vote_countdown','downgrade')
            registry='--registry' in sys.argv
            out=ROOT/('docs/ux-3/channel-migration-results.json' if registry else 'docs/ux-3/migration-results.json')
            out.parent.mkdir(parents=True,exist_ok=True)
            out.write_text(json.dumps({'migration':'0029_room_channels' if registry else '0028_private_message_positions','checks':([
                'common/announcement/vote and participant DM backfilled',
                'teams from templates, snapshots, overrides and history backfilled',
                'identities deduplicated with unique UUIDs',
                'source events, text and IDs unchanged; no unread event created',
                'downgrade and re-upgrade keep all source data'
                ] if registry else [
                'upgrade with existing untouched, edited and deleted private messages',
                'positions backfilled from original events and last revision',
                'source IDs, text and revision history preserved',
                'two pagination indexes exist', 'downgrade and re-upgrade preserve data']),
                'result':'pass','database':'separate temporary PostgreSQL14 database, deleted afterwards'},indent=2)+'\n')
            print('UX3 migration: 5 checks passed',flush=True)
        finally:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))

if __name__=='__main__':main()
