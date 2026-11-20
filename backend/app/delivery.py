"""Persist recipient-scoped invalidations in the event transaction, never from a GET.

Only explicit, known private event kinds acquire a player audience. Unknown private
kinds remain host-only. No delivery contains content, event kind or a public ordinal.
"""
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import POSTGAME_POLICY_VERSION
from app.db.models import GameEvent, Room, RoomChannel, RoomDelivery, RoomDeliveryStream, RoomMember

SELF_ACTIONS = {
    'player.kill', 'player.revive', 'player.add_effect', 'player.remove_effect',
    'player.change_role', 'player.change_faction', 'player.change_team',
}
REQUEST_KINDS = {'role.request_submitted', 'role.request_reviewed', 'role.request_withdrawn'}
POLICY_KINDS = {'player.change_team', 'player.change_role'}


def player_audience(kind: str, visibility: str, payload: dict, active: set[str], actor: str | None) -> set[str]:
    """Audience from immutable send-time metadata, restricted to authorized members."""
    if kind in {'chat.sent', 'chat.revised', 'chat.reaction_changed'}:
        channel = payload.get('channel')
        if channel == 'common':
            return set(active)
        if channel == 'team':
            return active.intersection(payload.get('recipient_ids', []))
        if channel == 'host':
            return active.intersection({payload.get('participant_user_id')})
        return set()
    if kind == 'player.private_message_sent':
        return active.intersection({payload.get('recipient_user_id')})
    if kind in REQUEST_KINDS | SELF_ACTIONS | {'chat.mute_changed'}:
        return active.intersection({payload.get('request', {}).get('user_id')})
    if kind == 'vote.ballot_submitted':
        return active.intersection({actor})
    if kind in {'roles.draft_chosen', 'roles.draft_offered'}:
        return active.intersection({payload.get('user_id')})
    if kind == 'roles.distributed':
        return active.intersection(item.get('user_id') for item in payload.get('assignments', []))
    if kind == 'roles.draft_started':
        return set(active)
    return set(active) if visibility == 'room' else set()


def invalidation(kind: str, visibility: str, payload: dict) -> tuple[list[str], tuple[str, str] | None]:
    """Small, public-safe refresh scopes; channel identities still need an audience."""
    if kind in {'chat.sent', 'chat.revised', 'chat.reaction_changed'}:
        channel = payload.get('channel')
        key = ('common', '') if channel == 'common' else (
            ('team', payload.get('team_label')) if channel == 'team' else
            ('moderator_dm', payload.get('participant_user_id')) if channel == 'host' else None)
        return ['channels', 'chat'], key
    if kind == 'player.private_message_sent' or kind in REQUEST_KINDS:
        recipient = payload.get('recipient_user_id') if kind == 'player.private_message_sent' else payload.get('request', {}).get('user_id')
        return ['channels', 'chat'] if kind == 'player.private_message_sent' else ['channels', 'role_requests'], ('moderator_dm', recipient)
    if kind == 'chat.mute_changed':
        return ['channels'], None
    if kind in SELF_ACTIONS:
        return ['channels', 'roles', 'room'], None
    if kind.startswith('roles.'):
        return ['channels', 'roles', 'room'], None
    if kind.startswith('vote.'):
        return ['announcements', 'vote'] if visibility == 'room' else ['vote'], ('vote', '')
    if kind in {'content.notice_edited', 'content.notice_deleted', 'player.death_announced',
                'player.revival_announced', 'announcement.published'}:
        return ['announcements', 'room'], ('announcement', '')
    if visibility == 'room':
        return ['announcements', 'channels', 'roles', 'room', 'vote'], None
    return ['management'], None


async def record_deliveries(db: AsyncSession, room: Room, event: GameEvent) -> None:
    # append_event already holds the room lock; concurrent writers cannot allocate
    # an earlier position after a later one commits. No mutation of existing deliveries.
    members = (await db.scalars(select(RoomMember).where(RoomMember.room_id == room.id,
        RoomMember.left_at.is_(None), RoomMember.consent_version == POSTGAME_POLICY_VERSION))).all()
    active = {str(member.user_id) for member in members}
    audience = player_audience(event.kind, event.visibility, event.payload, active,
        str(event.actor_user_id) if event.actor_user_id else None) | {str(room.owner_user_id)}
    scopes, identity = invalidation(event.kind, event.visibility, event.payload)
    channel = await db.scalar(select(RoomChannel.id).where(RoomChannel.room_id == room.id,
        RoomChannel.kind == identity[0], RoomChannel.channel_key == identity[1])) if identity and identity[1] is not None else None
    target = event.payload.get('request', {}).get('user_id')
    rows = [{'room_id': room.id, 'user_id': UUID(user_id), 'position': 1,
             'policy_epoch': 2 if event.kind in POLICY_KINDS and user_id == target else 1}
            for user_id in sorted(audience)]
    statement = insert(RoomDeliveryStream).values(rows)
    allocated = (await db.execute(statement.on_conflict_do_update(
        index_elements=[RoomDeliveryStream.room_id, RoomDeliveryStream.user_id],
        set_={'position': RoomDeliveryStream.position + 1,
              'policy_epoch': RoomDeliveryStream.policy_epoch + statement.excluded.policy_epoch - 1})
        .returning(RoomDeliveryStream.user_id, RoomDeliveryStream.position))).all()
    db.add_all([RoomDelivery(id=uuid4(), room_id=room.id, user_id=user_id, position=position,
        source_event_id=event.id, scopes=scopes, channel_ids=[str(channel)] if channel else [])
        for user_id, position in allocated])
    await db.flush()
