# UX3 · Lượt 7 — read/unread trên server và xác nhận theo viewport

Ngày 2026-11-20. Tiếp nối [realtime dùng chung](shared-realtime.md). Lượt này hoàn thành read state cho **chat chung, chat đội và DM với quản trò**. Badge không còn phụ thuộc marker trong sessionStorage hoặc việc một item đã tải vào bộ nhớ.

Cập nhật sau lượt này: [lượt 8 đã thêm reaction cho chat](reactions.md), head hiện tại là **0032_message_reactions**. Các số liệu và migration 0031 bên dưới ghi kết quả riêng của lượt 7.

## Hành vi

- Server giữ mốc đã đọc riêng theo phòng, tài khoản và UUID kênh. Hai phiên trình duyệt dùng chung mốc này; vòng refresh catalog 6 giây đồng bộ badge sang phiên khác. Tab nền hoặc lỗi mạng có thể làm cập nhật chậm hơn.
- “Mới” chỉ xuất hiện khi còn tin **do người khác gửi**, được ghi delivery cho người xem và nằm sau mốc đọc của kênh. Không lấy hiệu hai số thứ tự toàn phòng. Tin tự gửi, sửa tin đã đọc, request/vote kín hoặc ghi chú không tạo badge chat mới.
- Tin bị xóa không còn tính unread. Xóa tin mới nhất không làm mất badge nếu vẫn còn tin chưa đọc trước đó. Sửa tin chưa đọc giữ trạng thái chưa đọc của bản gốc.
- Frontend chỉ gửi ACK cho item xuất hiện thực trong vùng cuộn. Tin đã tải nhưng nằm dưới viewport vẫn chưa đọc. Với hàng ngắn, ít nhất nửa hàng phải hiển thị; với hàng cao hơn viewport phải thấy phần cuối. Đây là quy ước hiển thị để ACK, không thể đo người dùng có đọc hiểu hay không.
- Document ẩn hoặc có dialog đang mở không ACK chat bị che. Đóng dialog/hiện lại document đánh giá viewport lại. Tải lịch sử, giữ anchor và nhận tin khi đang đọc cũ không tự đánh dấu tin dưới màn hình.
- Mốc là “đã đọc tới item nhìn thấy”: nhảy tới cuối có thể xác nhận cả những tin trước item đó. Server không cần lưu từng tin đã nhìn và không cho client tự đặt số thứ tự.
- ACK được gộp trong 250 ms, mỗi kênh có một request đang chạy. Lỗi mạng/5xx thử lại sau 3 giây. Không xóa badge lạc quan trước khi server xác nhận; sau ACK tải catalog mới để không ghi đè badge của tin vừa đến bằng response cũ.

Không công khai read receipt cho người gửi hoặc quản trò. ACK không thêm GameEvent/outbox/delivery, không đổi room version/cursor và không xuất hiện như một thao tác điều hành trong transcript.

## Schema và API

Migration mới **0031_channel_reads**, thêm `channel_reads(room_id, user_id, channel_id, position, updated_at)`. Position là checkpoint delivery riêng của actor, chỉ ở server. Khóa chính actor/kênh và atomic upsert `greatest` bảo đảm tab chậm hoặc retry không kéo mốc lùi. Xóa phòng/kênh cascade read state.

ChannelRef v2 có `unread: boolean`; không trả mốc, timestamp đọc hay vị trí của người khác. `last_activity_id` vẫn giữ để tương thích các client v2 trước đó, nhưng UI mới không dùng trường này làm nguồn badge. Route context numeric legacy chưa được loại bỏ.

```http
POST /api/v2/rooms/{room_id}/channels/{channel_id}/read
Content-Type: application/json

{"visible_item_id":"private_message:11111111-1111-4111-8111-111111111111"}
```

ID hợp lệ có dạng `chat:<uuid>` hoặc `private_message:<uuid>`; response thành công là `204`. API xác thực phiên, quyền kênh và source trong đúng phòng/kênh. Team cũ cần recipient snapshot thật; biết UUID của tin từ trước khi mình vào đội không đủ để ACK. Không nhận `position`, actor hoặc cursor tự khai. Backend kiểm tra lại quyền sau upsert để xử lý kick trong lúc request chờ; checkpoint bị rollback khi quyền đã mất.

ACK chỉ tiến tới delivery của **đúng source**; tin đến sau source trong lúc request đang chờ vẫn unread. Public history trước lúc tham gia có thể đọc mà không có delivery: ACK đó không tạo hoặc tiến mốc. Sau trận, các kênh chat hiển thị không-unread cho lịch sử mở rộng, ACK không tạo mốc cho những DM người này chưa từng được nhận.

Việc source được render trong viewport được thực thi ở frontend. Server chứng minh quyền/source/delivery; không thể chứng minh một client tùy biến thực sự hiển thị pixel. Một client tự ACK tin được phép xem chỉ tác động mốc riêng của họ.

## Migration và vận hành

Dừng API/worker và các tác vụ ghi phòng, dùng đúng `DATABASE_URL` của môi trường cần cập nhật:

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/alembic -c backend/alembic.ini check
```

Head cần có là **0031_channel_reads**; sau đó khởi động lại API/frontend. Lượt này chỉ áp dụng migration trên PostgreSQL thử nghiệm riêng, không tự thay DB ứng dụng đang chạy.

Tại thời điểm upgrade, lịch sử đang có được khởi tạo mốc theo delivery cuối cùng của từng actor/kênh, nên không xuất hiện hàng loạt badge từ backfill. Các marker local cũ không thể suy ra đầy đủ trên server; chính sách rollout là coi lịch sử đã tồn tại trước migration là đã đọc cho mục đích badge. Nội dung nguồn/transcript không đổi. Tin gửi sau nâng cấp bắt đầu theo read state mới.

Backfill nối message source với delivery đã lưu, không suy team từ role hiện tại và không cấp mốc cho DM/team chưa từng được nhận. Downgrade bỏ mốc đọc; re-upgrade khởi tạo lại baseline, không khôi phục chính xác trạng thái unread của phiên trước. Các thao tác này chỉ được thử trên DB riêng.

## Kiểm chứng

| Phạm vi | Kết quả |
| --- | --- |
| Toàn bộ backend | **78 test đạt**, gồm 8 test read state mới |
| Frontend | **5 file test đạt**, gồm 5 ca viewport/ACK queue mới |
| [Browser read state mới](read-browser-results.json) | **10 nhóm đạt**, không có page error |
| Hồi quy realtime / registry-cursor / chat / khung UX2 | **57 nhóm đạt** (11 + 15 + 17 + 14) |
| [Migration](read-migration-results.json) | **8 kiểm tra đạt**, DB riêng được xóa sau kiểm thử |
| Build/schema | TypeScript + Vite đạt; Alembic check không có thay đổi mới |

Tổng cộng 67 nhóm browser đạt. Ca badge DM trong script registry đã được cập nhật để cuộn item vào viewport trước khi kỳ vọng ACK; việc item tồn tại trong DOM không còn đồng nghĩa đã đọc.

Các nhóm test mới:

- Backend: read state bền vững và không có event phụ, DM hai nguồn trùng UUID, ACK đồng thời không lùi, edit/delete, quyền/extra field/postgame, team cũ/người mới, tin đến trong lúc ACK chờ, kick rollback, lịch sử trước khi tham gia và source khác phòng.
- Frontend: hình học viewport, hidden/dialog, gộp request, ACK đang chờ, retry, abort khi đổi identity.
- Chromium: item đã tải nhưng ngoài màn, hai phiên đồng bộ, xóa sessionStorage/reload, dialog, mô phỏng visibilityState, đổi kênh nhanh không ACK sai scope, delayed ACK đua tin mới, retry lỗi mạng, hoạt động kín không ảnh hưởng, xóa tin chưa nhìn thấy.

Artifact: [read-browser-results.json](read-browser-results.json), [read-migration-results.json](read-migration-results.json). Lệnh kiểm tra API/migration:

```bash
DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:55432/ma_soi_ux2 APP_ENV=local PYTHONPATH=backend .venv/bin/pytest backend/tests -q
DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:55432/ma_soi_ux2 APP_ENV=local PYTHONPATH=backend .venv/bin/python scripts/check_ux3_read_migration.py
npm test --prefix frontend
npm run build --prefix frontend
```

Browser dùng `scripts/check_ux3_reads.py` cùng API 8026/frontend preview 4176 và DB disposable localhost:55432. Bộ hồi quy gồm `check_ux3_realtime.py`, `check_ux3_channels.py --opaque`, `check_ux3.py`, `check_ux2.py`.

## Phần tiếp theo

Read/unread cho Thông báo, Vote public và request item cần source timeline của UX4/UX3.11; hiện các kênh đặc biệt chưa dùng endpoint ACK chat này. Chưa đánh dấu toàn bộ UX3.9c.3 hoàn thành.

Read state không thay thế phần virtualization/restore anchor UX3.7b/8. Chưa ngừng API legacy, chưa đóng audit metadata hay cổng F/G, chưa đo lại 500 người sau khi thêm read ACK/catalog query.
