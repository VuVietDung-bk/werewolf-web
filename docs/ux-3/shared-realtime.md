# UX3 · Lượt 6 — realtime dùng chung từ backend đến giao diện

Ngày 2026-11-20. Tiếp nối [delivery theo người nhận](recipient-delivery.md). Lượt này đưa feed đó vào WebSocket v2 và một provider dùng chung ở cấp phòng, thay các socket riêng trong App/ChatPanel. Không thêm migration; head vẫn là **0030_room_deliveries**.

Cập nhật sau lượt này: [lượt 7](server-read-state.md) bổ sung read/unread server và ACK theo viewport cho common/team/DM, cần migration `0031_channel_reads`. Các hướng dẫn và số liệu bên dưới ghi lại phạm vi lượt 6.

## Trải nghiệm và đồng bộ

- Trong một tab trình duyệt có một kết nối phòng, giữ nguyên khi đổi Chat / Role / Quản lý, đổi đội/DM hoặc mở Vote/Thông báo. Mỗi tab trình duyệt khác vẫn có kết nối riêng.
- Thanh phòng hiển thị trạng thái kết nối; màn nhỏ dùng chấm màu kèm nội dung cho trình đọc màn hình. Khi WebSocket ngắt, client đọc HTTP recovery, drain các trang còn thiếu rồi nối lại bằng checkpoint hiện tại.
- Chat đang mở chỉ hỏi changes khi invalidation thuộc kênh đó; sidebar cập nhật catalog/quyền. Role, draft, yêu cầu và phản hồi, vote, thông báo và bàn quản trò nhận tín hiệu riêng, kể cả khi public room version không thay đổi.
- Dialog yêu cầu role của người chơi nằm trong cùng provider. Tin chat vẫn là tin chat; chưa biến request thành item DM của UX3.11.
- Đổi quyền đọc/đội/role hoặc kết thúc trận có thể yêu cầu resync. Client bỏ cache phân trang và catalog cũ, tải lại snapshot và giữ nháp theo user/phòng/kênh. Chưa phục hồi trang chứa scroll anchor qua mọi trường hợp resync/reload; phần đó vẫn thuộc UX3.7b/8.
- Kick/mất quyền đóng socket và đưa về trang vào làng; phiên bị thu hồi đưa về đăng nhập. Cleanup dừng timer, abort HTTP đang chờ, bỏ token trong phiên, vô hiệu callback/socket cũ. Logout/demo cũng dọn các key cursor legacy đã lưu trước đây.

Các vòng polling chat/quyền và refresh phòng hiện có vẫn là fallback bổ sung. Không chuyển việc quyết định role/vote/hiệu ứng sang client hoặc tự xử lý kết quả thay quản trò.

## WebSocket v2

Endpoint: `WS /api/v2/rooms/{room_id}/live?cursor=<opaque>`. Cùng purpose `room_live` và checkpoint với `GET /api/v2/rooms/{room_id}/sync`, nên có thể chuyển giữa HTTP và WS mà không đổi nguồn replay.

Frame server:

```ts
{ type: 'ready', cursor: string }
{ type: 'invalidation', deliveries: { id: string; scopes: string[]; channel_ids: string[] }[], cursor: string }
{ type: 'resync_required' }
```

Không có global sequence, event ID, kind, nội dung chat, role hoặc payload phiếu trong frame v2. Cursor dài 114 ký tự, ràng buộc phòng/user/purpose/epoch và có TTL theo lượt 4. `ready` khi reconnect với token hợp lệ giữ nguyên checkpoint đầu vào; feed bắt đầu replay sau checkpoint đó. Khi không có delivery được phép xem thì không phát frame hoặc reissue token do hoạt động của người khác.

Token sai/hết hạn/đổi epoch/đi trước DB sau restore: gửi `resync_required`, đóng `4409`. Hết phiên: `4401`; mất quyền: `4403`; phòng không tồn tại: `4404`; query protocol sai: `4400`. Lệnh ghi gửi qua WS bị đóng `1003`; mọi command vẫn qua HTTP và kiểm tra quyền/idempotency hiện có.

## Backend và kiểm tra quyền

Một feed task phục vụ các kết nối của cùng phòng trong mỗi API process. Redis/outbox đánh thức feed; PostgreSQL polling bù khi hint hoặc Redis mất. Lấy tối đa 50 delivery cho từng người nhận bằng LATERAL query có giới hạn, dùng vị trí riêng và index đã có; thứ tự trả lại được chỉ rõ. Không quét và đếm các event kín rồi trả numeric room boundary.

Feed kiểm tra session, membership, consent và epoch cả khi phòng không có sự kiện mới. Quyền được truy vấn hàng loạt cho idle connections; trước mỗi batch có nội dung sắp gửi, backend đọc lại session/membership/head/epoch trong một câu SQL mới sau khi đã tải pending rows. Kick hoặc đổi đội trong khoảng chờ sẽ đóng/resync trước khi enqueue batch. Mỗi lần gửi có timeout 2 giây; consumer chậm bị đóng `1013`, còn socket khác được xử lý song song. Metrics hiện cộng cả legacy và v2, không đếm phòng trùng hai lần.

Đây là kiểm tra quyền trước khi enqueue frame, không thể thu hồi byte đã được gửi vào mạng trước một thao tác thu hồi quyền đến sau đó. Frame chỉ có invalidation; API lấy dữ liệu vẫn tự xác thực quyền.

## Frontend và các race đã xử lý

`RoomRealtimeClient` sở hữu cursor chung, retry, polling, dedup và lifecycle; hook/provider React phân phối scope xuống component. Dedup giới hạn 2.048 delivery ID; các tín hiệu được gom trong 40 ms để giảm tải lại trùng. Ready/reconnect làm mới các khu vực cần thiết; resync còn xóa cache/quyền cũ trước khi lấy snapshot.

HTTP đang chờ không được ghi đè cursor hoặc scope sau khi một socket mới/WS frame đã tiến checkpoint: mỗi response phải khớp generation đang hoạt động. Callback từ socket đã thay thế bị bỏ qua. Khi unmount/đổi tài khoản, AbortController cùng generation chặn response cũ cập nhật identity mới. Opaque cursor chỉ được giữ/gửi nguyên chuỗi; không dùng `Number` hoặc `Math.max`.

Một reset chung có nghĩa cập nhật mọi kênh đang mở, ngay cả khi coalesce cùng batch chỉ chứa invalidation của một DM khác. Các panel dùng tín hiệu để chạy lại effect tải dữ liệu đã có guard, thay vì phụ thuộc duy nhất vào public room version.

## Chạy và kiểm chứng

| Phạm vi | Kết quả |
| --- | --- |
| Toàn bộ backend | **70 test đạt**, gồm 7 ca WebSocket v2 |
| Frontend | **4 file test đạt**, gồm 8 ca transport mới |
| [Chromium realtime mới](realtime-browser-results.json) | **11 nhóm đạt**, không có page error |
| [Hồi quy registry/cursor](cursor-browser-results.json) | **15 nhóm đạt** |
| [Hồi quy chat UX3](browser-results.json) | **17 nhóm đạt** |
| [Hồi quy khung UX2](../ux-2/browser-results.json) | **14 nhóm đạt**, gồm desktop/mobile 320–1440 px |
| Build và schema | TypeScript/Vite đạt; Alembic check không có thay đổi mới |

Restart API và frontend sau khi cập nhật. Nếu chưa nâng từ lượt 5, dừng các writer và chạy migration tới `0030_room_deliveries` theo [hướng dẫn](recipient-delivery.md#migration-và-triển-khai). Key ring vẫn dùng `CHAT_CURSOR_KEYS`/`CHAT_CURSOR_ACTIVE_KEY`; local một worker có thể dùng khóa tạm. Không cần Redis để chạy bộ kiểm thử này; đường polling PostgreSQL đã được dùng trong browser.

Tổng cộng 57 nhóm browser đạt trong lượt này. Các test mới:

- Backend WS: replay kết hợp HTTP, batch giới hạn, scope/token/protocol, sự kiện kín không tạo frame, logout ở phòng idle, epoch chỉ đổi đúng actor, kick/đổi đội sau khi tải frame chờ, consumer chậm không chặn người khác.
- Transport frontend: coalesce/dedup, response HTTP cũ tới sau WS mới, fallback drain/reconnect, resync/bootstrap, abort và callback từ identity cũ, revocation ngừng retry.
- Chromium/API thật: giữ một socket khi đổi tab, private activity không đổi frame/token người khác, HTTP recovery giữ nháp, cursor hỏng, đổi role kín không tăng public version, đổi đội, request/review, vote/thông báo, kick và hết phiên.

Lệnh kiểm thử (chỉ DB thử nghiệm riêng đã migration):

```bash
DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:55432/ma_soi_ux2 APP_ENV=local PYTHONPATH=backend .venv/bin/pytest backend/tests -q
npm test --prefix frontend
npm run build --prefix frontend
```

Browser chạy `scripts/check_ux3_realtime.py`, `scripts/check_ux3_channels.py --opaque`, `scripts/check_ux3.py` và `scripts/check_ux2.py`, với API local 8026/frontend preview 4176 và DB thử nghiệm `ma_soi_ux2` ở localhost:55432. Các script chỉ dọn dữ liệu do chúng tạo. Artifact mới: [realtime-browser-results.json](realtime-browser-results.json).

## Còn lại

Chưa ngừng các route numeric legacy hoặc loại metadata ở context/request/thông báo; client mới không gọi WS legacy nhưng client khác vẫn có thể gọi đường cũ. Vì vậy audit metadata vẫn mở, chưa đánh dấu toàn UX3.9 hay cổng F/G hoàn thành.

Vị trí đọc server/viewport ACK, reaction, virtualization với 10.000 tin, restore anchor đầy đủ và request item trong DM vẫn theo checklist. Chưa đo lại mục tiêu 500 người trên realtime mới; kết quả tải của kiến trúc cũ không thay cho phép đo này.
