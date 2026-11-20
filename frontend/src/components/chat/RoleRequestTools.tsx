import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, jsonBody, type LinkableRoleAction, type RoleRequest, type RoleRequestContext, type Room } from '../../api'
import { useRequestIds } from '../../useRequestIds'
import { useChatDraft } from './state'
import type { DisplayMessage } from './presentation'
import { pendingChatRequest } from './pending'

export const requestStatus:Record<string,string>={pending:'Chờ xử lý',in_review:'Đang xét',accepted:'Đã chấp nhận',rejected:'Đã từ chối',withdrawn:'Đã rút'}
export function RoleRequestRow({item,busy,onReview,onWithdraw}:{item:DisplayMessage;busy:boolean;onReview:()=>void;onWithdraw:()=>void}){
  const request=item.original?.request
  if(!request)return null
  return <li className="u3-message u3-group-start u3-request" data-item-key={item.key}>
    <span className="u3-avatar" aria-hidden="true">{item.authorName.slice(0,1).toUpperCase()}</span>
    <div className="u3-message-content">
      <div className="u3-message-heading"><strong>{item.authorName}</strong><time dateTime={item.createdAt}>{new Date(item.createdAt).toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit'})}</time></div>
      <div className="u3-request-label">Yêu cầu dùng role · {request.role_name}</div>
      <p>{item.text}</p><span className="u3-request-status">{requestStatus[request.status]??request.status}</span>
      {request.reply_text&&<p className="u3-request-reply"><strong>Quản trò:</strong> {request.reply_text}</p>}
      {request.can_review&&<button type="button" className="u3-request-action" disabled={busy} onClick={onReview}>Xử lý yêu cầu</button>}
      {request.can_withdraw&&<button type="button" className="u3-request-action" disabled={busy} onClick={onWithdraw}>Rút yêu cầu</button>}
      {item.original?.history&&<a className="u3-request-action" href="#management/history">Lịch sử yêu cầu sau trận</a>}
    </div>
  </li>
}

export function RequestComposer({room,userId,onClose,onSent}:{room:Room;userId:string;onClose:()=>void;onSent:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null)
  const [context,setContext]=useState<RoleRequestContext|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('')
  const scope=JSON.stringify([userId,room.id,'role-request'])
  const [text,setText,ackDraft]=useChatDraft(scope)
  const requestIdFor=useRequestIds(),base=`/api/rooms/${room.id}/role-requests`
  useEffect(()=>{dialog.current?.showModal();return()=>dialog.current?.close()},[])
  useEffect(()=>{
    let active=true
    const load=()=>void api<RoleRequestContext>(`${base}/context`).then(value=>{if(active)setContext(value)}).catch(cause=>{if(active){setContext(null);setError(cause.message)}})
    load();const timer=setInterval(load,6000)
    return()=>{active=false;clearInterval(timer)}
  },[base,room.version])
  async function send(event:FormEvent){
    event.preventDefault();if(busy||!context?.can_submit||!text.trim())return
    const sent=text,request=requestIdFor({user_id:userId,room_id:room.id,role_request:{text:text.trim()}})
    const pending=pendingChatRequest(scope,{role_request:{text:text.trim()}},request.id)
    setBusy(true);setError('')
    try{
      await api(base,{method:'POST',body:jsonBody({client_request_id:pending.id,text:text.trim()})})
      request.acknowledge();pending.acknowledge();ackDraft(sent);onSent();onClose()
    }catch(cause){setError(cause instanceof Error?cause.message:'Không gửi được yêu cầu.')}finally{setBusy(false)}
  }
  return <dialog ref={dialog} className="settings-dialog" aria-labelledby="u3-request-title" onCancel={event=>{event.preventDefault();if(!busy)onClose()}}>
    <div className="settings-dialog-header"><h2 id="u3-request-title">Yêu cầu dùng role</h2><button type="button" disabled={busy} onClick={onClose} aria-label="Đóng yêu cầu role">✕</button></div>
    <form className="stack-form" onSubmit={event=>void send(event)}>
      <p>Yêu cầu và phản hồi xuất hiện trong DM này. Sau trận được công bố theo quy định phòng.</p>
      {context?.role_name&&<strong>{context.role_name}</strong>}
      <label htmlFor="role-request-text">Nội dung gửi quản trò</label>
      <textarea id="role-request-text" maxLength={2000} value={text} disabled={busy||!context?.can_submit} onChange={event=>setText(event.target.value)}/>
      {context&&!context.can_submit&&<p role="status">{context.reason}</p>}
      {error&&<p role="alert" className="form-message">{error}</p>}
      <button disabled={busy||!context?.can_submit||!text.trim()}>{busy?'Đang gửi…':'Gửi yêu cầu'}</button>
    </form>
  </dialog>
}

export function RequestReviewDialog({roomId,requestId,onClose,onReviewed}:{roomId:string;requestId:string;onClose:()=>void;onReviewed:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null)
  const [item,setItem]=useState<RoleRequest|null>(null),[actions,setActions]=useState<LinkableRoleAction[]>([])
  const [status,setStatus]=useState('in_review'),[reply,setReply]=useState(''),[linked,setLinked]=useState<string[]>([])
  const [busy,setBusy]=useState(false),[error,setError]=useState('')
  const requestIdFor=useRequestIds(),base=`/api/rooms/${roomId}/role-requests/${requestId}`
  useEffect(()=>{dialog.current?.showModal();return()=>dialog.current?.close()},[])
  useEffect(()=>{
    let active=true
    void Promise.all([api<RoleRequest>(base),api<LinkableRoleAction[]>(`${base}/linkable-actions`)]).then(([request,choices])=>{
      if(active){setItem(request);setStatus(request.status==='in_review'?'accepted':'in_review');setReply(request.reply_text??'');setActions(choices)}
    }).catch(cause=>{if(active)setError(cause.message)})
    return()=>{active=false}
  },[base])
  async function review(event:FormEvent){
    event.preventDefault();if(!item||busy||!['pending','in_review'].includes(item.status))return
    const payload={status,reply_text:reply.trim()||null,linked_event_ids:status==='accepted'?linked:[]}
    const request=requestIdFor({room_id:roomId,role_request_id:requestId,review:payload})
    setBusy(true);setError('')
    try{await api(`${base}/review`,{method:'POST',body:jsonBody({...payload,client_request_id:request.id})});request.acknowledge();onReviewed();onClose()}
    catch(cause){setError(cause instanceof Error?cause.message:'Không xử lý được yêu cầu.')}finally{setBusy(false)}
  }
  return <dialog ref={dialog} className="settings-dialog" aria-labelledby="u3-review-title" onCancel={event=>{event.preventDefault();if(!busy)onClose()}}>
    <div className="settings-dialog-header"><h2 id="u3-review-title">Xử lý yêu cầu</h2><button type="button" disabled={busy} onClick={onClose} aria-label="Đóng xử lý yêu cầu">✕</button></div>
    {error&&<p role="alert" className="form-message">{error}</p>}
    {item&&<form className="stack-form" onSubmit={event=>void review(event)}>
      <strong>{item.sender_name} · {item.role_name}</strong><p>{item.text}</p>
      <label>Trạng thái<select value={status} onChange={event=>setStatus(event.target.value)}>
        {item.status==='pending'&&<option value="in_review">Đang xét</option>}<option value="accepted">Chấp nhận</option><option value="rejected">Từ chối</option>
      </select></label>
      <label>Phản hồi cho người gửi<textarea maxLength={2000} value={reply} onChange={event=>setReply(event.target.value)}/></label>
      {status==='accepted'&&actions.length>0&&<fieldset><legend>Thao tác đã thực hiện (nếu có)</legend>
        {actions.map(action=><label key={action.event_id}><input type="checkbox" checked={linked.includes(action.event_id)}
          disabled={busy||(!linked.includes(action.event_id)&&linked.length>=10)} onChange={event=>setLinked(current=>event.target.checked?[...current,action.event_id]:current.filter(id=>id!==action.event_id))}/>
          {({'player.kill':'Giết','player.revive':'Hồi sinh','player.add_effect':'Thêm hiệu ứng','player.remove_effect':'Bỏ hiệu ứng','player.change_role':'Đổi role','player.change_faction':'Đổi phe','player.change_team':'Đổi đội chat'} as Record<string,string>)[action.kind]??'Thao tác'} · {action.target_name} · {new Date(action.created_at).toLocaleTimeString('vi-VN')}</label>)}
      </fieldset>}
      <button disabled={busy||!['pending','in_review'].includes(item.status)||(status!=='in_review'&&!reply.trim())}>Lưu phản hồi</button>
    </form>}
  </dialog>
}
