"""Authenticated, actor/channel/purpose-bound chat cursors. No ordinal is public."""
import base64
import binascii
import json
import os
import re
import secrets
import struct
import time
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException

# Fixed size regardless of position, actor or phase. AEAD associated data binds all scope IDs.
BODY = struct.Struct('>QQQQ16s')
TTL_SECONDS = 3600
TOKEN_LENGTH = 114
TOKEN_PATTERN = re.compile(r'c1\.[A-Za-z0-9]{8}\.[A-Za-z0-9_-]{102}\Z')


def resync_required() -> HTTPException:
    return HTTPException(status_code=409, detail={'code':'chat_resync_required',
        'message':'Lịch sử chat cần đồng bộ lại. Vui lòng tải lại kênh.'})


@dataclass(frozen=True)
class Cursor:
    created: int
    updated: int
    before: int
    issued: int
    epoch: bytes


class CursorCodec:
    def __init__(self, keys: dict[str, bytes], active: str):
        if (not 1 <= len(keys) <= 4 or active not in keys or any(
            not re.fullmatch(r'[A-Za-z0-9]{8}', key_id) or len(key) != 32 for key_id,key in keys.items())):
            raise ValueError('CHAT_CURSOR_KEYS requires 1–4 named 32-byte keys; IDs are 8 alphanumeric characters')
        self.keys = {key_id:AESGCM(key) for key_id,key in keys.items()}
        self.active = active

    @staticmethod
    def aad(room_id: UUID, user_id: UUID, channel_id: UUID, purpose: str) -> bytes:
        return b'ma-soi-chat-cursor-v1:' + room_id.bytes + user_id.bytes + channel_id.bytes + purpose.encode('ascii')

    def decode(self, token: str, room_id: UUID, user_id: UUID, channel_id: UUID, purpose: str, epoch: bytes) -> Cursor:
        try:
            # Check before base64/JSON parsing. Invalid input never appears in an error payload.
            if len(token) != TOKEN_LENGTH or not TOKEN_PATTERN.fullmatch(token):
                raise ValueError('invalid token')
            _,key_id,encoded=token.split('.')
            data=base64.urlsafe_b64decode(encoded+'==')
            if base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii') != encoded:
                raise ValueError('non-canonical token')
            raw=self.keys[key_id].decrypt(data[:12],data[12:],self.aad(room_id,user_id,channel_id,purpose))
            cursor=Cursor(*BODY.unpack(raw))
            now=int(time.time())
            if (cursor.epoch != epoch or cursor.issued > now+30 or now-cursor.issued >= TTL_SECONDS
                or max(cursor.created,cursor.updated,cursor.before) > 2**63-1):
                raise ValueError('stale token')
            return cursor
        except (InvalidTag, ValueError, KeyError, struct.error, binascii.Error):
            raise resync_required() from None

    def encode(self, room_id: UUID, user_id: UUID, channel_id: UUID, purpose: str, epoch: bytes,
               *, created: int, updated: int, before: int = 0, reuse: str | None = None) -> str:
        if reuse:
            old=self.decode(reuse,room_id,user_id,channel_id,purpose,epoch)
            if (old.created,old.updated,old.before)==(created,updated,before):
                return reuse
        cursor=BODY.pack(created,updated,before,int(time.time()),epoch)
        nonce=secrets.token_bytes(12)
        encrypted=self.keys[self.active].encrypt(nonce,cursor,self.aad(room_id,user_id,channel_id,purpose))
        return f'c1.{self.active}.'+base64.urlsafe_b64encode(nonce+encrypted).rstrip(b'=').decode('ascii')


@lru_cache(maxsize=1)
def cursor_codec() -> CursorCodec:
    raw=os.environ.get('CHAT_CURSOR_KEYS','')
    active=os.environ.get('CHAT_CURSOR_ACTIVE_KEY','')
    if not raw:
        if os.environ.get('APP_ENV') != 'local':
            raise RuntimeError('Configure CHAT_CURSOR_KEYS and CHAT_CURSOR_ACTIVE_KEY before starting this environment')
        return CursorCodec({'local001':secrets.token_bytes(32)},'local001')
    try:
        configured=json.loads(raw)
        if not isinstance(configured,dict):
            raise ValueError('invalid key ring')
        keys={key_id:base64.b64decode(value,altchars=b'-_',validate=True) for key_id,value in configured.items()}
        return CursorCodec(keys,active)
    except (TypeError,ValueError,binascii.Error):
        raise RuntimeError('Invalid chat cursor key configuration') from None
