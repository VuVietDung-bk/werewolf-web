"""Read checkpoint backfill/constraints/cascade, on an isolated temporary database."""
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

ROOT=Path(__file__).resolve().parents[1]


def main():
    parsed=urlsplit(os.environ.get('DATABASE_URL',''))
    if parsed.hostname not in ('127.0.0.1','localhost') or parsed.port!=55432 or parsed.path!='/ma_soi_ux2':
        raise SystemExit('Use only local disposable ma_soi_ux2 at port 55432')
    name='ma_soi_read_migration_'+uuid4().hex[:10]
    admin_url=urlunsplit(parsed._replace(scheme='postgresql',path='/postgres'))
    test_url=urlunsplit(parsed._replace(scheme='postgresql',path='/'+name))
    env={**os.environ,'DATABASE_URL':test_url.replace('postgresql://','postgresql+psycopg://',1)}
    with psycopg.connect(admin_url,autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
        try:
            def migrate(target,direction='upgrade'):
                subprocess.run([str(ROOT/'.venv/bin/alembic'),'-c','backend/alembic.ini',direction,target],
                    cwd=ROOT,env=env,check=True,capture_output=True)
            migrate('0030_room_deliveries')
            host,a,b,room=[uuid4() for _ in range(4)]
            common,team,dm=[uuid4() for _ in range(3)]
            sources=[uuid4() for _ in range(4)]
            with psycopg.connect(test_url) as db:
                for uid in (host,a,b):db.execute('INSERT INTO users(id,display_name) VALUES(%s,%s)',(uid,'Read migration'))
                db.execute("INSERT INTO rooms(id,owner_user_id,code,name,seat_limit,status,version) VALUES(%s,%s,%s,'Read migration',2,'playing',4)",(room,host,uuid4().hex[:12]))
                for seat,uid in enumerate((a,b),start=1):
                    db.execute("INSERT INTO room_members(room_id,user_id,seat_number,consent_version,consented_at) VALUES(%s,%s,%s,'postgame-full-v1',now())",(room,uid,seat))
                for kind,key,cid in [('common','',common),('team','Former wolves',team),('moderator_dm',str(a),dm)]:
                    db.execute('INSERT INTO room_channels(id,room_id,kind,channel_key) VALUES(%s,%s,%s,%s)',(cid,room,kind,key))
                db.execute("INSERT INTO role_snapshots(room_id,set_name,definitions,distribution_mode) VALUES(%s,'Fixture','[]','random')",(room,))
                db.execute('INSERT INTO role_assignments(room_id,user_id,role_id) VALUES(%s,%s,%s)',(room,a,uuid4()))
                db.execute('INSERT INTO player_states(room_id,user_id) VALUES(%s,%s)',(room,a))
                for position,event in enumerate(sources,start=1):
                    db.execute("INSERT INTO game_events(id,room_id,sequence,actor_user_id,kind,visibility,payload) VALUES(%s,%s,%s,%s,%s,'host','{}')",(event,room,position,host,'player.private_message_sent' if position==3 else 'chat.sent'))
                    if position==3:
                        db.execute('INSERT INTO private_messages(id,room_id,user_id,sender_user_id,event_id,sequence,updated_sequence,text) VALUES(%s,%s,%s,%s,%s,3,3,%s)',(uuid4(),room,a,host,event,'Original private'))
                    else:
                        db.execute('INSERT INTO chat_messages(id,room_id,event_id,sequence,updated_sequence,channel,team_label,sender_user_id,recipient_ids,text) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                            (uuid4(),room,event,position,position,'team' if position==2 else 'common','Former wolves' if position==2 else None,host,Jsonb([str(a)] if position==2 else []),'Original chat'))
                # Different recipients have different positions; do not copy global sequence.
                for uid,events in [(host,[0,1,2,3]),(a,[0,1,2,3]),(b,[0,3])]:
                    db.execute('INSERT INTO room_delivery_streams(room_id,user_id,position) VALUES(%s,%s,%s)',(room,uid,len(events)))
                    for position,index in enumerate(events,start=1):
                        db.execute('INSERT INTO room_deliveries(id,room_id,user_id,position,source_event_id,scopes,channel_ids) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                            (uuid4(),room,uid,position,sources[index],Jsonb(['chat']),Jsonb([str([common,team,dm,common][index])])))
            expected={(host,common):4,(host,team):2,(host,dm):3,(a,common):4,(a,team):2,(a,dm):3,(b,common):2}
            for cycle in range(2):
                migrate('0031_channel_reads')
                with psycopg.connect(test_url) as db:
                    rows=db.execute('SELECT user_id,channel_id,position FROM channel_reads WHERE room_id=%s',(room,)).fetchall()
                    assert {(uid,cid):pos for uid,cid,pos in rows}==expected,rows
                    assert db.execute('SELECT count(*) FROM game_events WHERE room_id=%s',(room,)).fetchone()[0]==4
                    assert db.execute('SELECT id FROM game_events WHERE room_id=%s ORDER BY sequence',(room,)).fetchall()==[(event,) for event in sources]
                    assert db.execute('SELECT count(*) FROM outbox').fetchone()[0]==0
                    assert db.execute('SELECT text FROM private_messages WHERE room_id=%s',(room,)).fetchone()[0]=='Original private'
                    try:
                        with db.transaction():
                            db.execute('UPDATE channel_reads SET position=-1 WHERE room_id=%s',(room,))
                    except psycopg.errors.CheckViolation:pass
                    else:raise AssertionError('Negative read position accepted')
                if cycle==0:migrate('0030_room_deliveries','downgrade')
            with psycopg.connect(test_url) as db:
                db.execute('DELETE FROM rooms WHERE id=%s',(room,))
                assert db.execute('SELECT count(*) FROM channel_reads').fetchone()[0]==0
            result={'migration':'0031_channel_reads','result':'pass','checks':[
                'existing chat and private history initialized at delivered actor/channel boundary',
                'old team membership comes only from saved delivery, not current role',
                'recipient without team/DM deliveries receives no inferred read checkpoint',
                'read position uses private delivery order, not room sequence',
                'source text/IDs/events unchanged and no outbox event created',
                'negative positions rejected',
                'downgrade/re-upgrade preserves source data',
                'room retention cascades read checkpoints'],
                'database':'isolated temporary PostgreSQL14 database, deleted afterwards'}
            (ROOT/'docs/ux-3/read-migration-results.json').write_text(json.dumps(result,indent=2)+'\n')
            print('Read migration: 8 checks passed',flush=True)
        finally:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__=='__main__':main()
