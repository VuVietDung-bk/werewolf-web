import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { api, ApiError, jsonBody, type ChatChannel, type ChatContext, type ChatMessage, type TimelineItem, type TimelinePage, type ChangesPage, type ChatPolicy, type Room } from '../api'
import { useRequestIds } from '../useRequestIds'
import { useConfirmation } from '../useConfirmation'
import ChatGlyph from './ChatGlyph'
import type { ChatSelection } from '../roomNavigation'
import MessageComposer from './chat/MessageComposer'
import MessageTimeline from './chat/MessageTimeline'
import MessageRow from './chat/MessageRow'
import { RequestComposer, RequestReviewDialog, RoleRequestRow } from './chat/RoleRequestTools'
import { chatDisplay, messageKey, timelineMessage } from './chat/presentation'
import { useChatDraft } from './chat/state'
import { expandEmojiAliases } from './chat/emoji'
import { pendingChatRequest } from './chat/pending'
import { useRoomChannels } from './chat/ChannelContext'
import { applyChanges, prependHistory } from './chat/changes'
import { forgetPosition, positionFor, positionGeneration, rememberPosition } from './chat/positions'

const channelNames: Record<ChatChannel, string> = {
  common: 'Chat chung', team: 'Chat đội', host: 'Chat quản trò',
}
const channelTabNames: Record<ChatChannel, string> = {
  common: 'Chung', team: 'Đội', host: 'Quản trò',
}

export default function ChatPanel({ room, userId, selection, embedded = false }: { room: Room; userId: string; selection?: ChatSelection; embedded?: boolean }) {
  const [localChannel, setChannel] = useState<ChatChannel>('common')
  const channel = selection?.channel ?? localChannel
  const {context,catalog,refresh:refreshChannels,error:channelError,markRead,state:connectionState,subscribe} = useRoomChannels()
  const [policyDraft, setPolicyDraft] = useState<ChatPolicy | null>(null)
  const [policyReason, setPolicyReason] = useState('')
  const [mutePlayerId, setMutePlayerId] = useState(room.members[0]?.user_id ?? '')
  const [muteReason, setMuteReason] = useState('')
  const [internalTeamLabel, setTeamLabel] = useState('')
  const teamLabel = selection?.team ?? internalTeamLabel
  const [internalParticipantId, setParticipantId] = useState(room.members[0]?.user_id ?? '')
  const participantId = selection?.participant ?? internalParticipantId
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loadedScope, setLoadedScope] = useState('')
  const [followSentVersion, setFollowSentVersion] = useState(0)
  const pageCursor = useRef<string|null>(null)
  const resumeCursor=useRef<{generation:number;cursor:string}|null>(null)
  const readGeneration = useRef(0)
  const positionEpoch=useRef(positionGeneration())
  const historyFlight = useRef<{generation:number;revisions:Map<string,TimelineItem>;overflow:boolean}|null>(null)
  const pageCache = useRef(new Map<string, {messages:ChatMessage[];before:string|null;after:string|null;cursor:string|null}>())
  const cacheStatus = useRef(room.status)
  const [before,setBefore] = useState<string|null>(null)
  const [after,setAfter] = useState<string|null>(null)
  const afterRef=useRef<string|null>(null)
  const [loadingNewer,setLoadingNewer]=useState(false)
  const [latestVersion,setLatestVersion]=useState(0)
  const skipAnchor=useRef(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingText, setEditingText] = useState('')
  const [busy, setBusy] = useState(false)
  const [requestOpen,setRequestOpen]=useState(false)
  const [reviewId,setReviewId]=useState<string|null>(null)
  const [nowMs, setNowMs] = useState(Date.now())
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [resyncVersion,setResyncVersion] = useState(0)
  const [error, setError] = useState('')
  const refreshRef = useRef<() => Promise<void>>(async () => {})
  const settingsDialog = useRef<HTMLDialogElement>(null)
  const refreshContextRef = useRef<() => void>(() => {})
  const requestIdFor = useRequestIds()
  const { ask, confirmationDialog } = useConfirmation()
  const base = `/api/rooms/${room.id}/chat`

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if(!context)return
    setPolicyDraft((current)=>current??context.policy)
    setTeamLabel((current)=>current||context.current_team_label||context.team_labels[0]||'')
  },[context])
  refreshContextRef.current=()=>{void refreshChannels()}

  useEffect(() => {
    if (context && room.status !== 'finished' && channel === 'team' && context.team_labels.length === 0) setChannel('common')
  }, [channel, context, room.status])

  const selectedParticipant = room.is_host || room.status === 'finished' ? participantId : undefined
  const selectedTeam = channel === 'team' ? selection?.team ?? (room.is_host ? teamLabel : context?.current_team_label ?? undefined) : undefined
  const selectedChannel = catalog?.channels.find((item) => channel==='common' ? item.kind==='common'
    :channel==='team' ? item.kind==='team' && item.team_label===(selectedTeam??context?.current_team_label)
    :item.kind==='moderator_dm' && item.participant_user_id===(selectedParticipant??userId))
  const canRead=Boolean(selectedChannel?.capabilities.read)
  const viewScope = JSON.stringify([
    channel, channel === 'team' ? selectedTeam ?? null : null,
    channel === 'host' ? selectedParticipant ?? null : null,
  ])
  const draftScope = JSON.stringify([userId, room.id, viewScope])
  const timelineScope=JSON.stringify([userId,room.id,selectedChannel?.id??viewScope])
  const [text, setText, acknowledgeDraft] = useChatDraft(draftScope)

  const currentViewScope = useRef(viewScope)
  currentViewScope.current = viewScope
  useEffect(()=>{setRequestOpen(false);setReviewId(null)},[viewScope])

  const unread=(['common','team','host'] as ChatChannel[]).reduce((result,value)=>{
    result[value]=Boolean(catalog?.channels.some(item=>item.kind===(value==='host'?'moderator_dm':value)&&item.unread))
    return result
  },{} as Record<ChatChannel,boolean>)
  const timelineBase=`/api/v2/rooms/${room.id}/channels/${selectedChannel?.id}`
  function query(token?:string,direction='before'):string {
    return `${timelineBase}/timeline${token?`?${direction}=${encodeURIComponent(token)}`:''}`
  }

  useEffect(() => {
    let active = true
    const generation=++readGeneration.current
    // Finishing the game changes read permissions and reveals revision histories.
    // Fetch a fresh page rather than keeping the in-game projection in the cache.
    if (cacheStatus.current !== room.status) {
      pageCache.current.clear()
      cacheStatus.current = room.status
    }
    const cached=pageCache.current.get(viewScope)
    let cursor=cached?.cursor??null
    pageCursor.current=cursor
    let inFlight:Promise<void>|null=null
    let again=false
    setMessages(cached?.messages??[])
    setBefore(cached?.before??null)
    setAfter(cached?.after??null);afterRef.current=cached?.after??null
    setLoadedScope(cached?viewScope:'')
    setEditingId(null)
    setLoadingOlder(false)
    setLoadingNewer(false)
    setError('')
    if(!canRead)return()=>{active=false}
    async function sync(){
      while(active){
        try{
          if(resumeCursor.current?.generation===generation){cursor=resumeCursor.current.cursor;pageCursor.current=cursor;resumeCursor.current=null}
          if(!cursor){
            const saved=skipAnchor.current?undefined:positionFor(timelineScope)
            skipAnchor.current=false
            let page:TimelinePage
            try{page=await api<TimelinePage>(saved?.key&&!saved.atEnd?query(saved.key,'around_item'):query())}
            catch(cause){
              if(!(cause instanceof ApiError)||cause.status!==404||!saved?.key)throw cause
              if(!active)return
              forgetPosition(timelineScope)
              page=await api<TimelinePage>(query())
            }
            if(!active)return
            setMessages(page.items.map(timelineMessage));setBefore(page.next_before);setLoadedScope(viewScope)
            setAfter(page.next_after);afterRef.current=page.next_after
            cursor=page.next_changes;pageCursor.current=cursor
          }else{
            let more=true
            while(more&&active){
              const page:ChangesPage=await api<ChangesPage>(`${timelineBase}/changes?cursor=${encodeURIComponent(cursor!)}`)
              if(!active)return
              const flight=historyFlight.current
              if(flight?.generation===generation)for(const change of page.changes){
                if(!change.insert){
                  if(flight.revisions.size>=512&&!flight.revisions.has(change.item.id))flight.overflow=true
                  else flight.revisions.set(change.item.id,change.item)
                }
              }
              // A restored middle window must stay contiguous: forward pagination loads
              // the gap on demand. Revisions still update rows already in that window.
              if(page.changes.length)setMessages(current=>applyChanges(current,afterRef.current?
                page.changes.map(change=>({...change,insert:false})):page.changes))
              cursor=page.next_changes;pageCursor.current=cursor;more=page.has_more
            }
          }
          return
        }catch(cause){
          if(!active)return
          if(cause instanceof ApiError&&cause.code==='chat_resync_required'){
            active=false;readGeneration.current++;historyFlight.current=null
            cursor=null;pageCursor.current=null;pageCache.current.delete(viewScope)
            setMessages([]);setBefore(null);setLoadedScope('');setResyncVersion(value=>value+1);return
          }
          setLoadedScope(viewScope)
          setError(cause instanceof Error?cause.message:'Không tải được chat.');return
        }
      }
    }
    function refresh():Promise<void>{
      if(inFlight){again=true;return inFlight}
      inFlight=(async()=>{do{again=false;await sync()}while(active&&again)})().finally(()=>{inFlight=null})
      return inFlight
    }
    void refresh()
    refreshRef.current=refresh
    const timer=window.setInterval(()=>void refresh(),5000)
    return()=>{active=false;readGeneration.current++;historyFlight.current=null;window.clearInterval(timer);refreshRef.current=async()=>{}}
  },[base,channel,selectedParticipant,selectedTeam,selectedChannel?.id,canRead,viewScope,room.status,resyncVersion,latestVersion,timelineScope])

  useEffect(()=>{
    if(loadedScope!==viewScope)return
    pageCache.current.delete(viewScope)
    pageCache.current.set(viewScope,{messages,before,after,cursor:pageCursor.current})
    let total=[...pageCache.current.values()].reduce((sum,page)=>sum+page.messages.length,0)
    while(pageCache.current.size>1&&(pageCache.current.size>8||total>12000)){
      const key=pageCache.current.keys().next().value!
      total-=pageCache.current.get(key)!.messages.length;pageCache.current.delete(key)
    }
  },[messages,before,after,viewScope,loadedScope])

  useEffect(()=>subscribe(signal=>{
    if(signal.resync){
      readGeneration.current++;historyFlight.current=null;pageCache.current.clear();pageCursor.current=null
      setMessages([]);setBefore(null);setLoadedScope('');setResyncVersion(value=>value+1)
    }else if((signal.scopes.includes('chat')||signal.scopes.includes('role_requests'))&&(!signal.channelIds.length||signal.channelIds.includes(selectedChannel?.id??''))){
      void refreshRef.current()
    }
  }),[subscribe,selectedChannel?.id])

  function jumpLatest(){
    rememberPosition(timelineScope,{key:null,offset:0,top:0,atEnd:true},positionEpoch.current)
    readGeneration.current++;historyFlight.current=null;pageCache.current.delete(viewScope)
    pageCursor.current=null;afterRef.current=null;skipAnchor.current=true
    setAfter(null);setMessages([]);setLoadedScope('');setLatestVersion(value=>value+1)
  }

  async function loadOlder(direction:'before'|'after'='before') {
    const token=direction==='before'?before:after
    if (token === null || loadingOlder || loadingNewer || historyFlight.current) return
    const generation=readGeneration.current
    const flight={generation,revisions:new Map<string,TimelineItem>(),overflow:false}
    historyFlight.current=flight
    if(direction==='before')setLoadingOlder(true);else setLoadingNewer(true)
    try {
      const page = await api<TimelinePage>(query(token,direction))
      if(currentViewScope.current!==viewScope||generation!==readGeneration.current)return
      if(flight.overflow){
        pageCache.current.delete(viewScope);setMessages([]);setBefore(null);setLoadedScope('');setResyncVersion(value=>value+1);return
      }
      const revisions=new Map(flight.revisions)
      setMessages((current) => direction==='before'?prependHistory(current,page.items,revisions):
        applyChanges(current,page.items.map(item=>({item:revisions.get(item.id)??item,insert:true}))))
      if(direction==='before')setBefore(page.next_before)
      else{
        setAfter(page.next_after);afterRef.current=page.next_after
        if(!page.next_after){
          // Creations may have been skipped while this middle window had a gap.
          // Replay from its snapshot once the gap closes, including a creation
          // committed after the last forward page was read but before its ACK.
          resumeCursor.current={generation,cursor:page.next_changes}
          void refreshRef.current()
        }
      }
    } catch (cause) {
      if(generation!==readGeneration.current)return
      if(currentViewScope.current===viewScope&&cause instanceof ApiError&&cause.code==='chat_resync_required'){
        pageCache.current.delete(viewScope);pageCursor.current=null
        setMessages([]);setBefore(null);setLoadedScope('');setResyncVersion(value=>value+1);return
      }
      if(currentViewScope.current===viewScope)setError(cause instanceof Error ? cause.message : 'Không tải được tin cũ.')
    } finally {
      if(historyFlight.current===flight)historyFlight.current=null
      if(currentViewScope.current===viewScope&&generation===readGeneration.current){setLoadingOlder(false);setLoadingNewer(false)}
    }
  }

  function send() {
    if (busy || !canSend || !selectedChannel || slowRemaining>0 || !text.trim()) return
    const payload = { text: expandEmojiAliases(text.trim()) }
    const request = requestIdFor({ user_id:userId,room_id: room.id,channel_id:selectedChannel.id, payload })
    const legacyPayload = { channel, ...payload,
      ...(channel === 'team' && room.is_host ? {team_label:teamLabel} : {}),
      ...(channel === 'host' && room.is_host ? {participant_user_id:participantId} : {}) }
    const pending = pendingChatRequest(draftScope, {channel_id:selectedChannel.id,...payload}, request.id, [legacyPayload])
    setBusy(true)
    setError('')
    void (async () => {
      let sent = false
      try {
        await api(`/api/v2/rooms/${room.id}/channels/${selectedChannel.id}/messages`, { method: 'POST', body: jsonBody({ ...payload, client_request_id: pending.id }) })
        sent = true
        request.acknowledge()
        pending.acknowledge()
        acknowledgeDraft(text)
        if(currentViewScope.current===viewScope){
          if(afterRef.current||loadedScope!==viewScope)jumpLatest()
          setFollowSentVersion((value)=>value+1)
        }
        refreshContextRef.current()
        if(currentViewScope.current===viewScope)void refreshRef.current()
      } catch (cause) {
        if (currentViewScope.current !== viewScope) return
        setError(sent ? 'Tin đã gửi, nhưng chưa tải lại được. Vui lòng mở lại tab Chat để xem.'
          : cause instanceof Error ? cause.message : 'Không gửi được tin.')
      } finally { setBusy(false) }
    })()
  }

  async function reviseMessage(message: ChatMessage, action: 'edit' | 'delete') {
    const nextText = action === 'edit' ? editingText.trim() : null
    if (busy || (action === 'edit' && (!nextText || nextText === message.text))) return
    if (action === 'delete') {
      const confirmed = await ask({ title: 'Xóa tin nhắn',
        phase: `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}`,
        subject: channelNames[channel],
        audience: channel === 'common' ? 'Toàn phòng' : channel === 'team' ? `Đội ${selectedTeam}` : 'Người chơi nhận tin',
        publicText: 'Tin sẽ biến mất khỏi chat hiện tại. Nội dung gốc và thao tác xóa vẫn hiện trong lịch sử sau trận.',
        confirmLabel: 'Xóa tin' })
      if (!confirmed) return
    }
    const payload = { action, text: nextText }
    const request = requestIdFor({ room_id: room.id, source_kind:message.source_kind,source_id:message.id,channel_id:selectedChannel?.id, ...payload })
    setBusy(true)
    setError('')
    try {
      await api(`${timelineBase}/items/${message.source_kind}/${message.id}/revisions`, {
        method: 'POST', body: jsonBody({ ...payload, client_request_id: request.id }),
      })
      request.acknowledge()
      if (currentViewScope.current !== viewScope) return
      setEditingId(null)
      await refreshRef.current()
    } catch (cause) {
      if (currentViewScope.current === viewScope) setError(cause instanceof Error ? cause.message : 'Không sửa được tin nhắn.')
    } finally { setBusy(false) }
  }

  async function react(message:ChatMessage,emoji:string,present:boolean) {
    if(busy||!selectedChannel?.capabilities.react)return
    const identity={user_id:userId,room_id:room.id,channel_id:selectedChannel.id,
      source_kind:message.source_kind,source_id:message.id,emoji}
    // A newer opposite intent invalidates a lost-ACK command from the previous state.
    requestIdFor({...identity,present:!present}).acknowledge()
    const request=requestIdFor({...identity,present})
    setBusy(true);setError('')
    try{
      await api(`${timelineBase}/read`,{method:'POST',body:jsonBody({visible_item_id:messageKey(message)})})
      await api(`${timelineBase}/items/${message.source_kind}/${message.id}/reactions`,{
        method:present?'PUT':'DELETE',body:jsonBody({emoji,client_request_id:request.id}),
      })
      request.acknowledge()
      if(currentViewScope.current===viewScope)await refreshRef.current()
    }catch(cause){
      if(currentViewScope.current===viewScope){
        setError(cause instanceof Error?cause.message:'Không cập nhật được reaction.')
        refreshContextRef.current()
      }
    }finally{setBusy(false)}
  }

  async function withdrawRequest(message:ChatMessage){
    if(busy||!message.request?.can_withdraw)return
    if(!await ask({title:'Rút yêu cầu dùng role',phase:`${room.phase_type==='night'?'Đêm':'Ngày'} ${room.round_number}`,subject:message.text,audience:'Bạn và quản trò',
      publicText:'Yêu cầu sẽ được đánh dấu đã rút; nội dung vẫn giữ trong lịch sử.',confirmLabel:'Rút yêu cầu'}))return
    const request=requestIdFor({room_id:room.id,role_request_id:message.id,withdraw:true})
    setBusy(true);setError('')
    try{await api(`/api/rooms/${room.id}/role-requests/${message.id}/withdraw`,{method:'POST',body:jsonBody({client_request_id:request.id})});request.acknowledge();if(currentViewScope.current===viewScope)await refreshRef.current()}
    catch(cause){if(currentViewScope.current===viewScope)setError(cause instanceof Error?cause.message:'Không rút được yêu cầu.')}
    finally{setBusy(false)}
  }

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!policyDraft || busy) return
    const summary = [
      `Chat chung ban đêm: ${policyDraft.common_send_at_night ? 'cho gửi' : 'chỉ đọc'}`,
      `Chat đội ban ngày: ${policyDraft.team_send_in_day ? 'cho gửi' : 'chỉ đọc'}`,
      `Người chết gửi chat chung: ${policyDraft.dead_common_send ? 'có' : 'không'}`,
      `Người chết gửi chat đội: ${policyDraft.dead_team_send ? 'có' : 'không'}`,
      `Slow mode: ${policyDraft.slow_mode_seconds ? `${policyDraft.slow_mode_seconds} giây` : 'tắt'}`,
    ].join(' · ')
    const confirmed = await ask({ title: 'Đổi quyền chat',
      phase: room.status === 'playing' ? `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}` : 'Trước trận',
      subject: room.name, audience: 'Toàn phòng', publicText: summary,
      note: `${policyReason.trim() ? `Lý do: ${policyReason.trim()}. ` : ''}Quyền mới có hiệu lực ngay và được lưu vào sự kiện.`,
      confirmLabel: 'Lưu quyền chat' })
    if (!confirmed) return
    const payload = { ...policyDraft, reason: policyReason.trim() || null }
    const request = requestIdFor({ room_id: room.id, policy: payload })
    setBusy(true)
    setError('')
    let saved = false
    try {
      await api(`${base}/policy`, { method: 'PATCH', body: jsonBody({ ...payload, client_request_id: request.id }) })
      saved = true
      request.acknowledge()
      const next = await api<ChatContext>(`${base}/context`)
      await refreshChannels()
      setPolicyDraft(next.policy)
      setPolicyReason('')
    } catch (cause) {
      setError(saved ? 'Quyền chat đã lưu, nhưng chưa tải lại được. Vui lòng mở lại tab Chat để xem.'
        : cause instanceof Error ? cause.message : 'Không lưu được quyền chat.')
    } finally { setBusy(false) }
  }

  async function changeMute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!room.is_host || !mutePlayerId || !muteReason.trim() || busy) return
    const member = room.members.find((item) => item.user_id === mutePlayerId)
    const muted = !context?.muted_players.some((item) => item.user_id === mutePlayerId)
    const confirmed = await ask({ title: muted ? 'Chặn gửi chat' : 'Mở lại chat',
      phase: room.status === 'playing' ? `${room.phase_type === 'night' ? 'Đêm' : 'Ngày'} ${room.round_number}` : 'Trước trận',
      subject: member?.display_name ?? 'Người chơi', audience: 'Quản trò và người chơi này',
      publicText: muted ? 'Người chơi tạm thời chỉ đọc chat.' : 'Người chơi có thể gửi chat theo quyền role và pha.',
      note: `Lý do: ${muteReason.trim()}. Thao tác được lưu vào sự kiện.`,
      confirmLabel: muted ? 'Chặn gửi chat' : 'Mở lại chat' })
    if (!confirmed) return
    const payload = { muted, reason: muteReason.trim() }
    const request = requestIdFor({ room_id: room.id, mute_player_id: mutePlayerId, ...payload })
    setBusy(true)
    setError('')
    let saved = false
    try {
      await api(`${base}/players/${mutePlayerId}/mute`, { method: 'PATCH',
        body: jsonBody({ ...payload, client_request_id: request.id }) })
      saved = true
      request.acknowledge()
      await refreshChannels()
      setMuteReason('')
    } catch (cause) {
      setError(saved ? 'Đã đổi quyền chat, nhưng chưa tải lại được. Vui lòng mở lại tab Chat.'
        : cause instanceof Error ? cause.message : 'Không đổi được quyền chat.')
    } finally { setBusy(false) }
  }

  const canSend = Boolean(selectedChannel?.capabilities.send)
  const slowRemaining = room.is_host || !context?.next_chat_at ? 0
    : Math.max(0, Math.ceil((new Date(context.next_chat_at).getTime() - nowMs) / 1000))
  const timeline = useMemo(() => canRead && loadedScope === viewScope
    ? messages.map((message) => chatDisplay(message, room.host_id)) : [],
    [messages, room.host_id, loadedScope, viewScope, canRead])
  const readonlyReason = selectedChannel?.readonly_reason ?? (channelError
    ? 'Không tải được quyền kênh. Hãy cập nhật phòng để thử lại.'
    : catalog ? 'Kênh này hiện không có quyền truy cập.' : 'Đang tải quyền gửi tin…')

  return <section className={`chat-panel ${embedded ? 'chat-panel-embedded' : ''}`} aria-label={embedded ? 'Cuộc trò chuyện' : undefined} aria-labelledby={embedded ? undefined : 'chat-title'}>
    {embedded && <h2 id="chat-title" className="sr-only">Cuộc trò chuyện</h2>}
    {!embedded && <><span className="eyebrow">CHUYỆN TRONG LÀNG</span><h2 id="chat-title">Chat</h2></>}
    <small className="chat-connection" role="status">{connectionState === 'live' ? 'Đang nhận tin trực tiếp' : connectionState === 'connecting' ? 'Đang kết nối chat…' : 'Đang tự cập nhật tin mới'}</small>
    <p>{room.status === 'finished'
      ? 'Sau trận, mọi người từng tham gia xem được cả ba kênh và từng bản sửa/xóa trong lịch sử mỗi tin.'
      : 'Chat đội và chat với quản trò chỉ hiện cho người có quyền trong trận. Sau trận, toàn bộ chat được công bố cho người từng tham gia.'}</p>
    {context && <p className="chat-policy-summary">Quyền hiện tại: chat chung ban đêm {context.policy.common_send_at_night ? 'được gửi' : 'chỉ đọc'}; chat đội ban ngày {context.policy.team_send_in_day ? 'được gửi' : 'chỉ đọc'}; người chết {context.policy.dead_common_send ? 'được gửi chat chung' : 'chỉ đọc chat chung'} và {context.policy.dead_team_send ? 'được tham gia chat đội' : 'chỉ đọc tin đội cũ'}. Slow mode: {context.policy.slow_mode_seconds ? `${context.policy.slow_mode_seconds} giây giữa hai tin của mỗi người chơi` : 'tắt'}.</p>}
    {context?.is_muted && <p className="chat-mute-notice" role="status">Quản trò đã tạm chặn quyền gửi chat của bạn. Lý do: {context.mute_reason}</p>}
    {room.is_host && room.status !== 'finished' && <button type="button" className="button-secondary chat-settings-trigger"
      onClick={() => settingsDialog.current?.showModal()}>Cài đặt chat</button>}
    <div className={embedded ? 'chat-channel-tabs hide-embedded' : 'chat-channel-tabs'} role="group" aria-label="Kênh chat">
      {(['common', ...(room.status === 'finished' || context?.team_labels.length ? ['team'] : []), 'host'] as ChatChannel[]).map((item) => <button key={item} type="button"
        className={`${channel === item ? 'active ' : ''}${unread[item] ? 'has-unread' : ''}`} aria-pressed={channel === item}
        onClick={() => setChannel(item)}>
        <ChatGlyph channel={item} /><span className="chat-tab-label">{channelTabNames[item]}</span>
        {unread[item] && <span className="chat-unread" aria-label="Có tin mới">Mới</span>}
      </button>)}
    </div>
    {!embedded && channel === 'team' && room.is_host && room.status !== 'finished' && <label className="chat-picker">Đội chat
      <select value={teamLabel} onChange={(event) => setTeamLabel(event.target.value)}>
        {context?.team_labels.length ? context.team_labels.map((label) => <option key={label} value={label}>{label}{context.writable_team_labels.includes(label) ? '' : ' · lịch sử'}</option>)
          : <option value="">Chưa có đội chat</option>}
      </select></label>}
    {!embedded && channel === 'host' && room.is_host && room.status !== 'finished' && <label className="chat-picker">Người chơi
      <select value={participantId} onChange={(event) => setParticipantId(event.target.value)}>
        {room.members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
      </select></label>}
    {channel === 'team' && !room.is_host && room.status !== 'finished' && <p className="chat-scope">Đội hiện tại: {context?.current_team_label || 'Không có'} · Tin của các đội bạn từng tham gia vẫn đọc được.</p>}
    {channel === 'host' && !room.is_host && room.status !== 'finished' && <p className="chat-scope">Chỉ bạn và quản trò thấy trong trận. Tin quản trò gửi từ Bàn quản trò cũng ở đây; quyền trả lời tùy role của bạn.</p>}
    <MessageTimeline key={timelineScope} items={timeline} scope={timelineScope} loading={!catalog||(canRead&&loadedScope!==viewScope)}
      onVisibleItem={(item,receipt)=>{if(canRead&&selectedChannel)markRead(selectedChannel.id,item,receipt)}}
      loadingOlder={loadingOlder} hasOlder={before!==null} loadOlder={()=>void loadOlder()} followSentVersion={followSentVersion}
      hasLater={after!==null} loadingNewer={loadingNewer} loadNewer={()=>void loadOlder('after')} jumpLatest={jumpLatest} pinnedKey={editingId}
      renderRow={(item,grouped)=>item.kind==='request'?<RoleRequestRow key={item.key} item={item} busy={busy}
        onReview={()=>setReviewId(item.original!.id)} onWithdraw={()=>void withdrawRequest(item.original!)}/>:<MessageRow key={item.key} item={item} grouped={grouped} busy={busy}
        canEdit={Boolean(item.original?.actions.edit)}
        canReact={Boolean(selectedChannel?.capabilities.react&&!item.deleted)}
        onReaction={(emoji,present)=>{if(item.original)void react(item.original,emoji,present)}}
        editing={Boolean(item.original&&editingId===messageKey(item.original))} editText={editingText} onEditText={setEditingText}
        onEdit={()=>{if(item.original){setEditingId(messageKey(item.original));setEditingText(item.text)}}}
        onDelete={()=>{if(item.original)void reviseMessage(item.original,'delete')}}
        onSaveEdit={()=>{if(item.original)void reviseMessage(item.original,'edit')}} onCancelEdit={()=>setEditingId(null)} />} />
    {(error||channelError)&&<p className="form-message" role="alert">{error||channelError}</p>}
    {canSend?<MessageComposer value={text} onChange={setText} onSend={send} busy={busy}
      slowRemaining={slowRemaining} channelName={channelNames[channel].toLowerCase()} scope={draftScope}/>
      :<p className="chat-scope" role="status">{readonlyReason}</p>}
    {selectedChannel?.kind==='moderator_dm'&&!room.is_host&&room.status==='playing'&&<button type="button" className="u3-request-open" onClick={()=>setRequestOpen(true)}>Yêu cầu dùng role…</button>}
    {requestOpen&&<RequestComposer room={room} userId={userId} onClose={()=>setRequestOpen(false)} onSent={()=>void refreshRef.current()}/>}
    {reviewId&&<RequestReviewDialog roomId={room.id} requestId={reviewId} onClose={()=>setReviewId(null)} onReviewed={()=>void refreshRef.current()}/>}
    {room.is_host && room.status !== 'finished' && <dialog ref={settingsDialog} className="settings-dialog" aria-labelledby="chat-settings-title">
      <div className="settings-dialog-header"><h2 id="chat-settings-title">Cài đặt chat</h2>
        <button type="button" className="button-secondary" onClick={() => settingsDialog.current?.close()} aria-label="Đóng cài đặt chat">✕</button></div>
      {error && <p className="form-message" role="alert">{error}</p>}
      {policyDraft && <form className="chat-policy" onSubmit={(event) => void savePolicy(event)}>
      <h3>Chính sách chat của phòng</h3>
      <p>Trạng thái tạm dừng luôn chặn gửi tin. Quyền nhắn quản trò vẫn do từng role quyết định.</p>
      {([
        ['common_send_at_night', 'Cho gửi chat chung ban đêm'],
        ['team_send_in_day', 'Cho gửi chat đội ban ngày'],
        ['dead_common_send', 'Cho người chết gửi chat chung'],
        ['dead_team_send', 'Cho người chết gửi và nhận tin đội mới'],
      ] as [Exclude<keyof ChatPolicy, 'slow_mode_seconds'>, string][]).map(([key, label]) => <label key={key} className="consent">
        <input type="checkbox" checked={policyDraft[key]} onChange={(event) =>
          setPolicyDraft((current) => current ? { ...current, [key]: event.target.checked } : current)} />
        <span>{label}</span>
      </label>)}
      <label htmlFor="chat-slow-mode">Slow mode cho người chơi</label>
      <select id="chat-slow-mode" value={policyDraft.slow_mode_seconds} onChange={(event) =>
        setPolicyDraft((current) => current ? { ...current, slow_mode_seconds: Number(event.target.value) } : current)}>
        <option value={0}>Tắt</option>
        {Array.from({ length: 9 }, (_, index) => index + 2).map((seconds) =>
          <option key={seconds} value={seconds}>{seconds} giây</option>)}
      </select>
      <small>Áp dụng giữa hai tin của cùng người chơi trên mọi kênh. Quản trò không bị giới hạn.</small>
      <label htmlFor="chat-policy-reason">Lý do thay đổi (tùy chọn)</label>
      <input id="chat-policy-reason" maxLength={1000} value={policyReason}
        onChange={(event) => setPolicyReason(event.target.value)} />
      <button disabled={busy || JSON.stringify(policyDraft) === JSON.stringify(context?.policy)}>Lưu chính sách</button>
      </form>}
      <form className="chat-policy" onSubmit={(event) => void changeMute(event)}>
      <h3>Quản lý quyền gửi chat từng người</h3>
      <p>Người bị chặn vẫn đọc được các kênh họ có quyền xem. Chặn và mở lại đều cần lý do.</p>
      <label htmlFor="chat-mute-player">Người chơi</label>
      <select id="chat-mute-player" value={mutePlayerId} onChange={(event) => setMutePlayerId(event.target.value)}>
        {room.members.map((member) => <option key={member.user_id} value={member.user_id}>{member.display_name}</option>)}
      </select>
      {context?.muted_players.find((item) => item.user_id === mutePlayerId) &&
        <p className="chat-mute-notice">Đang bị chặn: {context.muted_players.find((item) => item.user_id === mutePlayerId)?.reason}</p>}
      <label htmlFor="chat-mute-reason">Lý do</label>
      <input id="chat-mute-reason" maxLength={1000} value={muteReason}
        onChange={(event) => setMuteReason(event.target.value)} />
      <button disabled={busy || !mutePlayerId || !muteReason.trim()}>
        {context?.muted_players.some((item) => item.user_id === mutePlayerId) ? 'Mở lại chat' : 'Chặn gửi chat'}
      </button>
      </form>
    </dialog>}
    {confirmationDialog}
  </section>
}
