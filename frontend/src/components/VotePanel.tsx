import { useEffect, useState, type FormEvent } from 'react'
import { api, jsonBody, type Room, type VoteHostRound, type VoteReview, type VoteRound } from '../api'
import { useRequestIds } from '../useRequestIds'
import { useConfirmation } from '../useConfirmation'

export default function VotePanel({ room, onRoomChanged }: {
  room: Room; onRoomChanged: () => Promise<void>
}) {
  const [vote, setVote] = useState<VoteRound | null>(null)
  const [latestVote, setLatestVote] = useState<VoteRound | null>(null)
  const [hostRounds, setHostRounds] = useState<VoteHostRound[]>([])
  const [selectedRoundId, setSelectedRoundId] = useState('')
  const [review, setReview] = useState<VoteReview | null>(null)
  const [targetId, setTargetId] = useState('')
  const [decisionText, setDecisionText] = useState('')
  const [decisionReason, setDecisionReason] = useState('')
  const [decisionTargetId, setDecisionTargetId] = useState('')
  const [includeResult, setIncludeResult] = useState(true)
  const [includeVoterNames, setIncludeVoterNames] = useState(false)
  const [includeReason, setIncludeReason] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const requestIdFor = useRequestIds()
  const { ask, confirmationDialog } = useConfirmation()
  const base = `/api/rooms/${room.id}/votes`

  useEffect(() => setSelectedRoundId(''), [room.id])

  async function fetchView(selectionId: string) {
    const latest = await api<VoteRound | null>(`${base}/latest`)
    const rounds = room.is_host ? await api<VoteHostRound[]>(`${base}/rounds`) : []
    const selected = room.is_host ? rounds.find((item) => item.id === selectionId) ?? rounds[0] ?? null : latest
    const nextReview = room.is_host && selected
      ? await api<VoteReview>(`${base}/${selected.id}/review`) : null
    return { latest, rounds, selected, nextReview }
  }

  function applyView(view: Awaited<ReturnType<typeof fetchView>>) {
    setLatestVote(view.latest)
    setHostRounds(view.rounds)
    setVote(view.selected)
    setReview(view.nextReview)
  }

  async function load(selectionId = selectedRoundId) {
    applyView(await fetchView(selectionId))
  }

  useEffect(() => {
    let active = true
    const refresh = () => { void fetchView(selectedRoundId)
      .then((view) => { if (active) { applyView(view); setError('') } })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : 'Không tải được vòng vote.') }) }
    refresh()
    const timer = window.setInterval(refresh, 8000)
    return () => { active = false; window.clearInterval(timer) }
  }, [base, room.version, room.is_host, selectedRoundId])

  useEffect(() => setTargetId(''), [vote?.id])
  useEffect(() => {
    setDecisionText(''); setDecisionReason(''); setDecisionTargetId('')
    setIncludeResult(true); setIncludeVoterNames(false); setIncludeReason(false)
  }, [vote?.id])

  async function run(path: string, payload: object, refreshRoom: boolean) {
    if (busy) return
    setBusy(true)
    setError('')
    const request = requestIdFor({ room_id: room.id, path, payload })
    let sent = false
    try {
      const result = await api<{ vote_round_id: string }>(path, {
        method: 'POST', body: jsonBody({ client_request_id: request.id, ...payload }),
      })
      sent = true
      request.acknowledge()
      const nextSelection = path === base ? result.vote_round_id : selectedRoundId
      if (path === base) setSelectedRoundId(nextSelection)
      await load(nextSelection)
      if (refreshRoom) await onRoomChanged()
    } catch (cause) {
      setError(sent ? 'Lệnh vote đã được ghi nhưng chưa tải lại được. Tải lại trang để kiểm tra.'
        : cause instanceof Error ? cause.message : 'Không thực hiện được lệnh vote.')
    } finally { setBusy(false) }
  }

  function cast(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!vote?.eligible || !targetId) return
    void run(`${base}/${vote.id}/ballots`, { choice: 'target', target_user_id: targetId }, false)
  }

  async function decide(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !vote || !decisionText.trim() || !decisionReason.trim()) return
    const target = room.members.find((member) => member.user_id === decisionTargetId)
    const text = decisionText.trim()
    const reason = decisionReason.trim()
    if (!await ask({ title: 'Chốt kết quả vote', phase: `Ngày ${vote.day_number}`,
      subject: target ? `Ghế ${target.seat_number} · ${target.display_name}` : 'Không gắn người chơi',
      audience: 'Chỉ quản trò trong trận',
      note: `Quyết định: ${text}\nLý do: ${reason}\nQuyết định này không tự giết người hoặc đổi pha.`,
      confirmLabel: 'Lưu quyết định' })) return
    await run(`${base}/${vote.id}/decision`, {
      decision_text: text, reason,
      target_user_id: decisionTargetId || null,
    }, false)
  }

  async function publish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !vote || !review?.decision || (!includeResult && !includeVoterNames && !includeReason)) return
    const summary = publicationPreview.join('\n')
    if (!await ask({ title: 'Công bố kết quả vote', phase: `Ngày ${vote.day_number}`,
      subject: `Vòng vote Ngày ${vote.day_number}`, audience: 'Toàn bộ thành viên phòng',
      publicText: summary, note: 'Chỉ các phần đã chọn được công bố; phiếu chi tiết vẫn kín trong trận.',
      confirmLabel: 'Công bố vào Thông báo' })) return
    await run(`${base}/${vote.id}/publication`, {
      include_result: includeResult, include_voter_names: includeVoterNames,
      include_reason: includeReason,
    }, true)
  }

  async function openRound() {
    if (busy) return
    if (!await ask({ title: 'Mở vòng vote', phase: `Ngày ${room.round_number}`,
      subject: room.name, audience: 'Toàn bộ thành viên phòng',
      publicText: `Vòng vote Ngày ${room.round_number} mở. Người sống có thể chọn người sống, bỏ qua hoặc chưa gửi phiếu.`,
      confirmLabel: 'Mở vòng vote' })) return
    await run(base, {}, true)
  }

  async function closeRound() {
    if (busy || !vote) return
    if (!await ask({ title: 'Đóng vòng vote', phase: `Ngày ${vote.day_number}`,
      subject: `Vòng vote Ngày ${vote.day_number}`, audience: 'Toàn bộ thành viên phòng',
      publicText: `Vòng vote Ngày ${vote.day_number} đã đóng. Người chơi không thể gửi hoặc đổi phiếu nữa.`,
      note: `Đã gửi hoặc bỏ qua: ${vote.submitted_count ?? 0} người. Có thể đóng dù chưa đủ phiếu.`,
      confirmLabel: 'Đóng vòng vote' })) return
    await run(`${base}/${vote.id}/close`, {}, true)
  }

  const currentDay = room.status === 'playing' && room.phase_type === 'day'
  const currentOpen = latestVote?.status === 'open' && latestVote.day_number === room.round_number
  const showingLatest = !vote || vote.id === latestVote?.id
  const canOpen = room.is_host && currentDay && !room.is_paused && !currentOpen && showingLatest
  const candidates = room.members.filter((member) => member.public_is_alive)
  const chosenName = room.members.find((member) => member.user_id === vote?.my_target_user_id)?.display_name
  const seatLabel = (userId: string | null) => {
    const member = room.members.find((item) => item.user_id === userId)
    return member ? `Ghế ${member.seat_number} · ${member.display_name}` : 'người chơi không còn trong phòng'
  }
  const publicationPreview = vote && review?.decision ? [
    `Vote Ngày ${vote.day_number}`,
    ...(includeResult ? [`Kết quả: ${review.decision.decision_text}`] : []),
    ...(includeVoterNames ? [`Đã gửi phiếu: ${review.ballots
      .filter((item) => item.choice === 'target' || item.choice === 'skip')
      .map((item) => `ghế ${item.seat_number} · ${item.display_name}`).join(', ') || 'không có ai'}`] : []),
    ...(includeReason ? [`Lý do: ${review.decision.reason}`] : []),
  ] : []

  return <section className="vote-panel" aria-labelledby="vote-title">
    <span className="eyebrow">BỎ PHIẾU THỦ CÔNG</span><h2 id="vote-title">Vote ban ngày</h2>
    <p>Quản trò mở, đóng và quyết định vote. Có thể vote từ Ngày 0; không cần đủ phiếu. Phiếu không tự giết ai hay chuyển pha.</p>
    {error && <p className="form-message" role="alert">{error}</p>}
    {room.is_host && hostRounds.length > 1 && <label className="vote-round-picker" htmlFor="vote-round-select">
      Xem vòng vote
      <select id="vote-round-select" value={selectedRoundId || hostRounds[0].id}
        onChange={(event) => setSelectedRoundId(event.target.value)}>
        {hostRounds.map((item, index) => <option key={item.id} value={item.id}>
          {index === 0 ? 'Mới nhất · ' : ''}Ngày {item.day_number} · {item.status === 'open' ? 'đang nhận' : item.published_at ? 'đã công bố' : item.decided_at ? 'đã chốt' : 'đã đóng'} · {new Date(item.opened_at).toLocaleTimeString('vi-VN')}
        </option>)}
      </select>
    </label>}
    {vote ? <div className="vote-state">
      <strong>Ngày {vote.day_number} · {vote.status === 'open' ? 'Đang nhận phiếu' : 'Đã đóng'}</strong>
      {room.is_host ? <p>Đã gửi hoặc bỏ qua: {vote.submitted_count ?? 0} người.{vote.status === 'open' ? ' Đóng vòng khi muốn ngừng nhận phiếu.' : ''}</p>
        : <p>Phiếu của bạn: {vote.my_choice === 'target' ? `chọn ${chosenName ?? 'người chơi'}`
          : vote.my_choice === 'skip' ? 'bỏ qua' : vote.my_choice === 'withdrawn' ? 'đã rút' : 'chưa gửi'}.
          Người chơi khác không thấy phiếu của bạn trong trận.</p>}
    </div> : <p>Chưa có vòng vote nào.</p>}
    {room.is_host ? <div className="inline-actions">
      {canOpen && <button type="button" disabled={busy} onClick={() => void openRound()}>Mở vòng vote</button>}
      {currentOpen && showingLatest && vote && <button type="button" disabled={busy} onClick={() => void closeRound()}>Đóng vòng vote</button>}
    </div> : vote?.eligible ? <>
      <form className="stack-form vote-form" onSubmit={cast}>
        <label htmlFor="vote-target">Chọn một người đang được công bố là sống</label>
        <select id="vote-target" value={targetId} onChange={(event) => setTargetId(event.target.value)}>
          <option value="">Chọn người chơi…</option>
          {candidates.map((member) => <option key={member.user_id} value={member.user_id}>
            Ghế {member.seat_number} · {member.display_name}
          </option>)}
        </select>
        <div className="inline-actions"><button disabled={busy || !targetId || (vote.my_choice === 'target' && vote.my_target_user_id === targetId)}>Gửi phiếu</button>
          <button type="button" className="button-secondary" disabled={busy || vote.my_choice === 'skip'}
            onClick={() => void run(`${base}/${vote.id}/ballots`, { choice: 'skip' }, false)}>Bỏ qua</button>
          {vote.my_choice && vote.my_choice !== 'withdrawn' && <button type="button" className="button-secondary"
            disabled={busy} onClick={() => void run(`${base}/${vote.id}/ballots`, { choice: 'withdraw' }, false)}>Rút phiếu</button>}
        </div>
      </form>
      <p>Bạn có thể đổi hoặc rút phiếu trước khi quản trò đóng vòng; không gửi phiếu cũng được.</p>
    </> : !room.is_host && currentOpen && <p>Bạn chưa có quyền gửi phiếu ở trạng thái hiện tại.</p>}
    {room.is_host && review && vote && <section className="vote-review" aria-labelledby="vote-review-title">
      <h3 id="vote-review-title">Tổng hợp phiếu · Ngày {vote.day_number}</h3>
      <p>{review.tally.length ? review.tally.map((item) => `${seatLabel(item.target_user_id)}: ${item.count}`).join(' · ')
        : 'Chưa có phiếu chọn người.'}</p>
      <p>Bỏ qua: {review.skip_count} · Đã rút: {review.withdrawn_count} · Chưa gửi: {review.not_submitted_count}</p>
      <ul className="vote-ballots">
        {review.ballots.map((ballot) => <li key={ballot.voter_user_id}>
          <span>Ghế {ballot.seat_number} · {ballot.display_name}</span>
          <strong>{ballot.choice === 'target' ? `Chọn ${seatLabel(ballot.target_user_id)}`
            : ballot.choice === 'skip' ? 'Bỏ qua'
              : ballot.choice === 'withdrawn' ? 'Đã rút'
                : ballot.public_is_alive ? 'Chưa gửi' : 'Không đủ điều kiện hiện tại'}</strong>
        </li>)}
      </ul>
      {review.decision ? <div className="vote-decision">
        <h4>Quyết định đã lưu</h4>
        <p>{review.decision.decision_text}</p>
        {review.decision.target_user_id && <p>Người liên quan: {seatLabel(review.decision.target_user_id)}</p>}
        <p>Lý do: {review.decision.reason}</p>
        <small>{review.publication_deleted ? 'Thông báo công khai đã xóa.'
          : review.publication_event_id ? 'Đã công bố những phần được chọn.' : 'Quyết định này chưa được công bố.'} Trạng thái người chơi chỉ đổi khi quản trò dùng thao tác riêng.</small>
      </div> : vote.status === 'closed' && room.status === 'playing' && <form className="stack-form vote-decision-form" onSubmit={decide}>
        <h4>Quản trò quyết định kết quả</h4>
        <label htmlFor="vote-decision-text">Kết quả</label>
        <textarea id="vote-decision-text" maxLength={2000} required value={decisionText}
          onChange={(event) => setDecisionText(event.target.value)}
          placeholder="Ví dụ: Không loại ai; loại ghế 2; hoặc xử lý khác theo role." />
        <label htmlFor="vote-decision-target">Người liên quan (tùy chọn)</label>
        <select id="vote-decision-target" value={decisionTargetId}
          onChange={(event) => setDecisionTargetId(event.target.value)}>
          <option value="">Không gắn người</option>
          {room.members.map((member) => <option key={member.user_id} value={member.user_id}>
            Ghế {member.seat_number} · {member.display_name}
          </option>)}
        </select>
        <label htmlFor="vote-decision-reason">Lý do</label>
        <textarea id="vote-decision-reason" maxLength={1000} required value={decisionReason}
          onChange={(event) => setDecisionReason(event.target.value)} />
        <p>Có thể chốt khi hòa, thiếu phiếu hoặc chọn khác số đông. Quyết định được lưu kín cho tới khi công bố.</p>
        <button disabled={busy || !decisionText.trim() || !decisionReason.trim()}>Lưu quyết định</button>
      </form>}
      {review.decision && (review.publication_deleted ? <p>Thông báo kết quả vote đã được xóa khỏi tab Thông báo.</p>
        : review.publication_event_id ? <p>Đã công bố kết quả trong tab <a href="#dead">Thông báo</a>. Nội dung có thể sửa hoặc xóa tại đó.</p>
        : room.status === 'playing' && <form className="vote-publication-form stack-form" onSubmit={publish}>
          <h4>Công bố kết quả vote (tùy chọn)</h4>
          <p>Chỉ những phần được đánh dấu mới xuất hiện trong Thông báo. Phiếu chi tiết và quyết định gốc vẫn được giữ kín trong trận.</p>
          <label className="vote-publication-option"><input type="checkbox" checked={includeResult}
            onChange={(event) => setIncludeResult(event.target.checked)} /> Kết quả</label>
          <label className="vote-publication-option"><input type="checkbox" checked={includeVoterNames}
            onChange={(event) => setIncludeVoterNames(event.target.checked)} /> Tên người đã gửi phiếu (không kèm lựa chọn)</label>
          <label className="vote-publication-option"><input type="checkbox" checked={includeReason}
            onChange={(event) => setIncludeReason(event.target.checked)} /> Lý do</label>
          {(includeResult || includeVoterNames || includeReason) && <div className="vote-publication-preview">
            <strong>Nội dung sẽ công bố</strong><p>{publicationPreview.join('\n')}</p>
          </div>}
          <button disabled={busy || (!includeResult && !includeVoterNames && !includeReason)}>Công bố vào Thông báo</button>
      </form>)}
    </section>}
    {confirmationDialog}
  </section>
}
