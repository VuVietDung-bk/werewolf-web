"""AEAD token scope, tampering, expiry, rotation and deployment configuration."""
import base64
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import chat_cursors as mod
from app.chat_cursors import CursorCodec, TOKEN_LENGTH


def test_cursor_encryption_scope_and_tampering(monkeypatch):
    codec=CursorCodec({'active01':bytes(range(32))},'active01')
    room,actor,channel=uuid4(),uuid4(),uuid4()
    epoch=b'allowed_epoch001'
    token=codec.encode(room,actor,channel,'changes',epoch,created=123456,updated=789012)
    assert len(token)==TOKEN_LENGTH
    decoded=codec.decode(token,room,actor,channel,'changes',epoch)
    assert (decoded.created,decoded.updated)==(123456,789012)
    assert codec.encode(room,actor,channel,'changes',epoch,created=123456,updated=789012,reuse=token)==token
    assert codec.encode(room,actor,channel,'changes',epoch,created=123456,updated=789012)!=token
    raw=base64.urlsafe_b64decode(token.split('.')[-1]+'==')
    assert b'123456' not in raw and actor.bytes not in raw
    invalid=[('',room,actor,channel,'changes',epoch),('x'*10000,room,actor,channel,'changes',epoch),
        (token,uuid4(),actor,channel,'changes',epoch),(token,room,uuid4(),channel,'changes',epoch),
        (token,room,actor,uuid4(),'changes',epoch),(token,room,actor,channel,'before',epoch),
        (token,room,actor,channel,'changes',b'changed_epoch001')]
    for index in (12,30,len(token)-1):
        replacement='A' if token[index]!='A' else 'B'
        invalid.append((token[:index]+replacement+token[index+1:],room,actor,channel,'changes',epoch))
    for args in invalid:
        with pytest.raises(HTTPException) as error:codec.decode(*args)
        assert error.value.status_code==409
        assert error.value.detail['code']=='chat_resync_required'
        assert token not in str(error.value.detail)
    monkeypatch.setattr(mod.time,'time',lambda:decoded.issued+mod.TTL_SECONDS)
    with pytest.raises(HTTPException):codec.decode(token,room,actor,channel,'changes',epoch)


def test_cursor_key_rotation_and_fail_closed_configuration(monkeypatch):
    ids=(uuid4(),uuid4(),uuid4(),'changes',b'allowed_epoch001')
    old=CursorCodec({'oldkey01':b'a'*32},'oldkey01')
    token=old.encode(*ids,created=1,updated=2)
    rotated=CursorCodec({'oldkey01':b'a'*32,'newkey01':b'b'*32},'newkey01')
    assert rotated.decode(token,*ids).updated==2
    assert rotated.encode(*ids,created=1,updated=2,reuse=token)==token
    assert rotated.encode(*ids,created=1,updated=3).startswith('c1.newkey01.')
    with pytest.raises(HTTPException):CursorCodec({'newkey01':b'b'*32},'newkey01').decode(token,*ids)
    mod.cursor_codec.cache_clear()
    try:
        monkeypatch.delenv('CHAT_CURSOR_KEYS',raising=False)
        monkeypatch.delenv('CHAT_CURSOR_ACTIVE_KEY',raising=False)
        monkeypatch.setenv('APP_ENV','staging')
        with pytest.raises(RuntimeError):mod.cursor_codec()
        monkeypatch.setenv('CHAT_CURSOR_KEYS','{"active01":"not-a-key"}')
        monkeypatch.setenv('CHAT_CURSOR_ACTIVE_KEY','active01')
        with pytest.raises(RuntimeError):mod.cursor_codec()
        monkeypatch.setenv('CHAT_CURSOR_KEYS','{"active01":"'+base64.urlsafe_b64encode(b'a'*32).decode()+'"}')
        configured=mod.cursor_codec()
        assert configured.decode(configured.encode(*ids,created=1,updated=2),*ids).updated==2
    finally:mod.cursor_codec.cache_clear()
