import { useEffect, useRef, type FormEvent } from 'react'

export type ConfirmationDetails = {
  title: string
  phase: string
  subject: string
  audience: string
  publicText?: string
  note?: string
  confirmLabel: string
}

export default function ConfirmationDialog({ details, settle }: {
  details: ConfirmationDetails | null
  settle: (confirmed: boolean) => void
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const closedByButton = useRef(false)

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (details && !dialog.open) {
      dialog.showModal()
      dialog.querySelector<HTMLButtonElement>('button[type="button"]')?.focus()
    }
    if (!details && dialog.open) dialog.close()
  }, [details])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    closedByButton.current = true
    dialogRef.current?.close()
    settle(true)
  }

  function cancel() {
    closedByButton.current = true
    dialogRef.current?.close()
    settle(false)
  }

  return <dialog ref={dialogRef} className="confirmation-dialog"
    aria-labelledby="confirmation-title" aria-describedby="confirmation-summary"
    onKeyDown={(event) => {
      if (event.key === 'Escape') { event.preventDefault(); cancel() }
    }}
    onCancel={(event) => { event.preventDefault(); cancel() }}
    onClose={() => {
      if (closedByButton.current) { closedByButton.current = false; return }
      settle(false)
    }}>
    {details && <form onSubmit={submit}>
      <div className="confirmation-body">
        <span className="eyebrow">KIỂM TRA TRƯỚC KHI THỰC HIỆN</span>
        <h2 id="confirmation-title">{details.title}</h2>
        <dl className="confirmation-facts">
          <div><dt>Pha</dt><dd>{details.phase}</dd></div>
          <div><dt>Đối tượng</dt><dd>{details.subject}</dd></div>
          <div><dt>Phạm vi</dt><dd>{details.audience}</dd></div>
        </dl>
        <div id="confirmation-summary" className="confirmation-preview">
          <strong>Nội dung người chơi sẽ thấy ngay</strong>
          <p>{details.publicText ?? 'Không có thông báo công khai từ thao tác này.'}</p>
        </div>
        {details.note && <p className="confirmation-note">{details.note}</p>}
      </div>
      <div className="confirmation-actions">
        <button type="button" className="button-secondary" autoFocus onClick={cancel}>Hủy</button>
        <button type="submit">{details.confirmLabel}</button>
      </div>
    </form>}
  </dialog>
}
