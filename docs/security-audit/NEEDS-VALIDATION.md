# NEEDS VALIDATION — các ứng viên chưa kết luận

Các mục dưới đây chưa được gán mức độ nghiêm trọng. Chỉ dùng dữ liệu giả và môi trường cô lập cho bước kiểm tra local; chủ vận hành chỉ đối chiếu cấu hình và số liệu tổng hợp, không tạo tải trên production.

**Cập nhật 2026-10-29:** Mã đã thêm nhãn quản trò trong chat, hạn mức lưu icon và vòng lặp xóa hết phòng đủ hạn. Những mục tương ứng bên dưới ghi lại chứng cứ ban đầu; cần kiểm tra hành vi bản sửa và đối chiếu cấu hình triển khai trước khi kết luận. Những mục về tạo phiên khách, export, WebSocket và đổi phiếu chưa được sửa ở lượt này.

## 1. Repeated guest signups can grow persistent identity tables

A client without a valid cookie can repeatedly create guest users and sessions. The repository does not show an application quota or cleanup for those rows. Capacity impact depends on ingress controls and retention in the deployed system.

**Ranh giới và nguyên nhân giả định:** Guest creation persists a fresh User and Session for each cookie-free request and no visible identity cleanup accompanies room retention.

**Đường đi nguồn:**

- [backend/app/main.py](../../backend/app/main.py#L169) — Cookie-free POST /api/auth/guest enters identity creation.
- [backend/app/main.py](../../backend/app/main.py#L176) — Only requests with an existing valid session reuse an identity.
- [backend/app/main.py](../../backend/app/main.py#L179) — A new User and Session are inserted and committed.

**Chứng cứ và giới hạn:**

- [backend/app/main.py](../../backend/app/main.py#L184) — The new rows are committed.
- [scripts/postgres_maintenance.sh](../../scripts/postgres_maintenance.sh#L104) — Room retention deletes rooms; no identity cleanup is visible here.

**Điều còn thiếu:**

- Production ingress rate limits and identity cleanup outside this repository are unknown.
- No isolated target run measured row growth or cross-user availability impact.

**Cách xác minh an toàn:**

- Local: With a disposable database and strict request cap, submit a small number of cookie-free requests and compare User and Session counts; inspect cleanup job behavior.
- Chủ vận hành: Review proxy rate limits, signup abuse metrics, and scheduled cleanup for expired sessions and orphaned users. Do not load-test production.

## 2. A player display name can resemble the moderator label

A guest may choose the display name “Quản trò”. In the chat timeline, a player message with that name and a genuine host message can render with the same bold sender text. Whether users can distinguish them through other cues requires a browser review.

**Ranh giới và nguyên nhân giả định:** Display names are not reserved and the chat sender line lacks a distinct host badge or seat marker.

**Đường đi nguồn:**

- [backend/app/main.py](../../backend/app/main.py#L72) — The guest name accepts arbitrary text within the length limit.
- [backend/app/main.py](../../backend/app/main.py#L179) — The chosen name is stored as the User display name.
- [frontend/src/components/ChatPanel.tsx](../../frontend/src/components/ChatPanel.tsx#L508) — The host label and player sender_name share the same element and can have identical text.

**Chứng cứ và giới hạn:**

- [frontend/src/components/ChatPanel.tsx](../../frontend/src/components/ChatPanel.tsx#L508) — No unique moderator badge is rendered beside the sender name in this line.
- [backend/app/main.py](../../backend/app/main.py#L73) — No reserved-name validator is visible in GuestInput.

**Điều còn thiếu:**

- The actual browser styling and contextual indicators were not inspected in an isolated runtime.
- Product policy may intentionally permit duplicate names and rely on a different trust cue.

**Cách xác minh an toàn:**

- Local: In a disposable room, create a player named Quản trò and compare their chat line with a real host message in the rendered UI, including mobile layout.
- Chủ vận hành: Decide the impersonation policy and ensure a server-derived host badge or reserved display names are consistently applied.

## 3. Full transcript export may exhaust worker memory

A permitted finished-room participant can request a full JSON or CSV export. The handler loads all matching events, builds a second list of serialized entries, then constructs the entire response in memory. A large transcript could stress the worker; practical impact is unmeasured.

**Ranh giới và nguyên nhân giả định:** The export path has no row or byte bound and does not stream results or response output.

**Đường đi nguồn:**

- [backend/app/transcript.py](../../backend/app/transcript.py#L235) — A permitted member requests a finished-room export.
- [backend/app/transcript.py](../../backend/app/transcript.py#L244) — All matching event rows are materialized.
- [backend/app/transcript.py](../../backend/app/transcript.py#L251) — The complete JSON response is serialized in memory; the CSV branch likewise uses StringIO.

**Chứng cứ và giới hạn:**

- [backend/app/transcript.py](../../backend/app/transcript.py#L241) — finished_room enforces permitted postgame access.
- [backend/app/transcript.py](../../backend/app/transcript.py#L246) — A second list of serialized rows is built.
- [backend/app/transcript.py](../../backend/app/transcript.py#L264) — CSV is returned from StringIO.getvalue().

**Điều còn thiếu:**

- Maximum legitimate transcript size and production memory limits are unknown.
- The audit could not run a bounded export in an isolated environment.

**Cách xác minh an toàn:**

- Local: On an isolated database, export a small fixed corpus at two bounded sizes while measuring peak worker memory; verify paging or streaming if added.
- Chủ vận hành: Compare maximum event count per room and worker memory budget; review export timeout, concurrency and gateway response limits without probing production.

## 4. Host icon uploads lack an aggregate storage budget

A room host can upload many individually valid icons. Each creates a database row with image bytes; creating a successor room copies every icon, including potentially unused icons. Total storage exposure depends on ingress and database capacity controls.

**Ranh giới và nguyên nhân giả định:** The upload has per-file size and dimension limits but no per-room count or total-byte quota; room replay copies all icon rows.

**Đường đi nguồn:**

- [backend/app/icons.py](../../backend/app/icons.py#L52) — An authenticated host uploads an icon to a room.
- [backend/app/icons.py](../../backend/app/icons.py#L66) — The accepted image is normalized and stored as bytes.
- [backend/app/rooms.py](../../backend/app/rooms.py#L416) — All prior room icon rows are copied into the successor room.

**Chứng cứ và giới hạn:**

- [backend/app/icons.py](../../backend/app/icons.py#L62) — Each file is capped at 1 MiB.
- [backend/app/icons.py](../../backend/app/icons.py#L67) — Each accepted upload creates a RoleIcon row.
- [backend/app/rooms.py](../../backend/app/rooms.py#L419) — Successor creation duplicates stored image bytes.

**Điều còn thiếu:**

- No deployed per-host upload quota, database storage cap, or rate limit is visible.
- A bounded isolated experiment was unavailable, so shared impact was not measured.

**Cách xác minh an toàn:**

- Local: In a disposable room with a small fixed upload count, measure row and byte growth and confirm whether unreferenced icons are copied.
- Chủ vận hành: Inspect actual upload rate limits and storage monitoring; set and verify per-room count or byte budgets before estimating production impact.

## 5. Daily retention can leave a backlog of eligible rooms

The sample daily schedule runs one retention batch of at most 100 finished or cancelled rooms. A sustained arrival rate above 100 eligible rooms per day would grow the backlog, leaving transcripts available to authorized former participants. Whether this breaches a promised deletion deadline depends on the public policy and actual scheduler.

**Ranh giới và nguyên nhân giả định:** The sample daily task invokes retention-delete once, and the deletion query is capped at 100 rooms without repeating until empty.

**Đường đi nguồn:**

- [deploy/maintenance.cron.example](../../deploy/maintenance.cron.example#L10) — The example invokes daily maintenance once per day.
- [scripts/postgres_maintenance.sh](../../scripts/postgres_maintenance.sh#L32) — Daily calls retention-delete 90 once.
- [scripts/postgres_maintenance.sh](../../scripts/postgres_maintenance.sh#L104) — The deletion query selects at most 100 eligible rooms.

**Chứng cứ và giới hạn:**

- [docs/step-9/backup-restore.md](../../docs/step-9/backup-restore.md#L24) — The runbook acknowledges that rooms above a batch wait for a later run.
- [README.md](../../README.md#L31) — Production scheduler installation is not evidenced by the repository.
- [backend/app/auth.py](../../backend/app/auth.py#L83) — Transcript access remains restricted to authorized room participants.

**Điều còn thiếu:**

- The production policy may define 90 days as eligibility rather than a maximum deletion deadline.
- Actual scheduler frequency, daily eligible-room count and backlog age are unknown.

**Cách xác minh an toàn:**

- Local: With disposable data, create 101 eligible rooms and run exactly one retention batch, then a second, checking remaining row counts.
- Chủ vận hành: Confirm the public deletion promise and installed schedule; inspect aggregate eligible-room count and age. Do not expose transcript contents.

## 6. A room member may accumulate WebSocket connections

A valid room member can open repeated WebSockets using one session. Each accepted connection stays registered until disconnect, and event polling iterates every connection. Shared availability impact depends on proxy and ASGI limits not visible here.

**Ranh giới và nguyên nhân giả định:** LiveHub.add does not enforce a per-session or per-room connection cap or application idle lifetime.

**Đường đi nguồn:**

- [backend/app/live.py](../../backend/app/live.py#L227) — A room member opens the WebSocket route.
- [backend/app/live.py](../../backend/app/live.py#L250) — Each accepted handshake creates a separate Connection.
- [backend/app/live.py](../../backend/app/live.py#L194) — The connection enters the room feed set without a cardinality check.
- [backend/app/live.py](../../backend/app/live.py#L105) — Each event is considered for every registered connection.

**Chứng cứ và giới hạn:**

- [backend/app/live.py](../../backend/app/live.py#L229) — Origin is checked.
- [backend/app/live.py](../../backend/app/live.py#L239) — Room access is checked.
- [backend/app/live.py](../../backend/app/live.py#L257) — The accepted socket awaits client input or disconnect without an application idle deadline.
- [backend/app/live.py](../../backend/app/live.py#L265) — Normal disconnect removes the connection.

**Điều còn thiếu:**

- Production proxy, ASGI and OS connection limits and idle timeouts are unknown.
- No isolated bounded connection test measured resource use or impact.

**Cách xác minh an toàn:**

- Local: In an isolated environment with strict socket and time caps, open a small fixed number of idle sockets from one session, compare event fanout counts, then close them.
- Chủ vận hành: Inspect proxy and ASGI WebSocket limits, timeouts, worker file descriptors and connection metrics; do not generate production load.

## 7. Repeated ballot changes can accumulate host-only events

An eligible living player can change or withdraw a ballot repeatedly during an open vote. Each accepted change with a fresh request ID appends an event and outbox row while locking the room. Other users may experience contention or storage growth if ingress permits high request rates.

**Ranh giới và nguyên nhân giả định:** The ballot endpoint validates each mutation but does not enforce per-voter rate or total-change limits.

**Đường đi nguồn:**

- [backend/app/votes.py](../../backend/app/votes.py#L362) — An eligible player submits a vote change.
- [backend/app/votes.py](../../backend/app/votes.py#L368) — The mutation obtains a row lock on the room.
- [backend/app/votes.py](../../backend/app/votes.py#L412) — Every distinct accepted change appends a host-only GameEvent and Outbox entry.

**Chứng cứ và giới hạn:**

- [backend/app/votes.py](../../backend/app/votes.py#L373) — Only reuse of the same request ID is deduplicated.
- [backend/app/votes.py](../../backend/app/votes.py#L401) — Only unchanged ballots are rejected.
- [backend/app/db/events.py](../../backend/app/db/events.py#L35) — Appending an event also inserts an Outbox row.

**Điều còn thiếu:**

- The deployed ingress rate limit and vote duration are unknown.
- No isolated bounded run established actual contention or shared availability impact.

**Cách xác minh an toàn:**

- Local: In an isolated test room, submit a small fixed sequence of alternating skip and withdraw choices with new request IDs; compare event and outbox counts.
- Chủ vận hành: Review per-user write limits and worker metrics during vote windows; do not generate load against production.
