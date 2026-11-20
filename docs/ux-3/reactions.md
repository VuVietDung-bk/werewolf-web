# UX3 · Lượt 8 — reaction cho chat chung, đội và quản trò

Ngày 2026-11-20. Tiếp nối [read state server](server-read-state.md). Lượt này hoàn thành UX3.6 cho các nguồn chat `ChatMessage` và `PrivateMessage`, từ database/API đến giao diện và realtime. Reaction trên Thông báo sẽ đi cùng timeline mới ở UX4; Vote và request trạng thái không nhận reaction.

Cập nhật tiếp theo: [lượt 9 đã hoàn thành cửa sổ render và khôi phục mốc đọc](virtual-history.md). Các số liệu bên dưới thuộc riêng lượt 8.

## Trải nghiệm

- Hover/focus một tin hoặc chạm nút trên mobile để mở bộ chọn emoji. Bộ chọn hiện có hỗ trợ tìm tên/alias, bàn phím và Escape; reaction dùng cùng **50 lựa chọn Unicode** đã kiểm tra trên server. Emoji ghép như `👨‍👩‍👧‍👦` là một giá trị, không bị cắt theo byte/code unit. Tin chat vẫn cho phép dán Unicode như trước; reaction hiện chưa nhận emoji ngoài catalog này.
- Các chip gọn dưới tin hiển thị emoji và tổng số người chọn. Chip của mình có trạng thái chọn; bấm để thêm/gỡ riêng reaction của mình. Hai người chọn cùng emoji thành một chip đếm 2, không tạo hai hàng.
- Reaction bám quyền **gửi** của kênh: pha, mute, pause, role bị động, đội cũ và quyền người chết đều theo policy chat hiện có. Ví dụ người chết mặc định không react ở chat chung, nhưng có thể phản hồi trong DM quản trò đã mở nếu `reply_allowed` cho phép. Khi kênh chỉ đọc hoặc trận kết thúc, vẫn xem số đếm nhưng không thêm/gỡ.
- Trước lệnh reaction, UI xác nhận đã đọc đúng item được người dùng chọn. API yêu cầu read checkpoint khi source có delivery của actor. Với public history trước lúc tham gia không có delivery riêng, vẫn kiểm tra quyền source, không tự nhảy mốc đọc tới tin mới hơn.
- Không cập nhật số đếm lạc quan từ response cũ; client lấy lại changes theo scope. Chuyển kênh khi request đang chờ không chèn kết quả sang DM/đội khác. Nháp composer giữ nguyên.
- Retry mất ACK dùng lại command ID. Khi người dùng thực hiện ý định ngược lại, command ID cũ của trạng thái trước được bỏ để lần thêm tiếp theo là một lệnh mới, không replay một lần thêm đã bị gỡ.

Reaction không biến thành yêu cầu dùng role, không tự áp hiệu ứng/giết người/vote và không yêu cầu nhập lý do hay confirm.

## API và dữ liệu

Head mới **0032_message_reactions**. `message_reactions` lưu trạng thái hiện tại, primary key `(source_event_id, user_id, emoji)`, FK/cascade theo source/phòng và index theo room/user. Hai source trùng UUID giữa bảng chat và private vẫn độc lập vì liên kết event nguồn thật ở server.

```http
PUT /api/v2/rooms/{room}/channels/{channel}/items/{chat|private_message}/{source_id}/reactions
DELETE /api/v2/rooms/{room}/channels/{channel}/items/{chat|private_message}/{source_id}/reactions
Content-Type: application/json

{"client_request_id":"11111111-1111-4111-8111-111111111111","emoji":"🐺"}
```

Response: `{"emoji":"🐺","present":true}` hoặc `false` theo lệnh đã được xác nhận. Actor lấy từ cookie. Không nhận actor/room/role tự khai trong body. UUID source phải thuộc đúng phòng, kênh và audience; biết UUID không mở quyền đọc/react. Tin bị xóa không nhận lệnh mới.

Timeline/changes v2 trả thêm `reactions: [{emoji, count, mine}]` và `actions.react`. Sidebar/capability cho phép UI phản ánh quyền mới ngay khi unmute/đổi pha, dù snapshot item cũ đã tải trong lúc bị khóa. API vẫn kiểm tra lại quyền dưới room lock.

Tóm tắt reaction được lấy theo batch các source đang có trong trang, không trả danh sách actor/recipient, ID event hay ordinal toàn phòng. Khi thay đổi thật, `updated_sequence` nội bộ của source tiến để changes bắt được tin cũ; client vẫn chỉ dùng opaque cursor và thứ tự server. No-op không đổi source checkpoint.

## Transaction, quyền và giới hạn

- Khóa phòng trước khi kiểm tra lại membership/current role/phase và ghi lệnh; request đang chờ sẽ bị từ chối nếu kick hoặc finish đã thắng trước.
- State, decision event, delivery đúng audience và outbox commit cùng transaction. Lỗi ở bước delivery làm rollback cả state, source checkpoint và event.
- Command cùng ID/cùng actor/cùng payload được ACK lại mà không thêm row/event. Dùng ID cho payload khác hoặc actor khác trả 409.
- Mỗi lệnh mới có `chat.reaction_decided` trong journal quản trò; chỉ khi trạng thái đổi mới thêm `chat.reaction_changed` và invalidation đúng nguồn. No-op có decision với `changed=false`, không phát như một thay đổi reaction cho player. Retry cùng ID không thêm decision.
- Max **8 emoji/người/tin**, **20 loại emoji/tin**. Đạt 20 loại vẫn cho người khác chọn loại đã có nếu họ chưa đạt 8.
- Rate limit **6 lệnh mới/10 giây**, **30/phút** theo actor/phòng trên các kênh reaction; 429 có `Retry-After`. Retry cùng ID được ACK trước kiểm tra rate. No-op với ID mới vẫn tính rate. Thời gian decision dùng clock lúc xử lý, để thời gian chờ room lock không làm event mới rơi khỏi cửa sổ rate.
- Giới hạn reaction độc lập với slow mode của tin nhắn; quyền gửi/mute/phase vẫn áp dụng. Không tăng thời gian chờ gửi tin chỉ vì thả reaction.

Reaction đội dùng recipient snapshot của tin gốc và membership/consent hiện tại khi ghi delivery. Người từng nhận nhưng đã đổi đội có thể đọc thay đổi trên tin cũ; người mới vào đội không nhận reaction của lịch sử chưa từng được cấp. DM chỉ đến participant và quản trò trong trận. Sau trận mở rộng theo consent như chat.

## Unread và lịch sử

Reaction không tạo ChatMessage/PrivateMessage mới, nên không tự tạo badge “Mới”. Tin gốc chưa đọc vẫn chưa đọc; sự kiện kín của B không đổi cursor/catalog/unread của A. Public reaction là hoạt động công khai của nguồn public, không phải quyết định thay đổi trạng thái trò chơi.

Gỡ reaction chỉ xóa projection hiện tại. Tin bị xóa khỏi live chat vẫn giữ event/reaction trong lịch sử sau trận. Transcript có nhãn “Thao tác reaction” / “Thêm hoặc gỡ reaction”, actor, thời gian, trạng thái trước/sau và liên kết source. Trong lịch sử tin sau trận có đường dẫn sang journal. Không nhúng toàn bộ lịch sử reaction không giới hạn vào từng item: nhật ký hiện có được phân trang và xuất JSON/CSV.

## Cập nhật và chạy kiểm thử

Dừng API/worker và các writer, đặt đúng `DATABASE_URL`, nâng schema rồi khởi động lại API/frontend:

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/alembic -c backend/alembic.ini check
```

Không thêm dependency. Lượt này chỉ migration DB thử nghiệm riêng. 0032 thêm bảng trống, không dựng reaction giả từ tin cũ, không đổi read checkpoint/nội dung/source ID. Downgrade xóa projection reaction hiện tại nhưng giữ journal nguồn; re-upgrade không tự khôi phục số đếm. Đây là kiểm tra tương thích schema, không phải quy trình rollback production.

Runner browser đã được đưa vào repo thay cho script tạm trong `/tmp`:

```bash
# Chỉ dùng DB disposable localhost:55432/ma_soi_ux2 đã migration.
DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:55432/ma_soi_ux2 .venv/bin/python scripts/run_ux3_browser.py
# Hoặc một nhóm; runner build rồi tự mở/dừng API 8026 + preview 4176.
DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:55432/ma_soi_ux2 .venv/bin/python scripts/run_ux3_browser.py --checks reactions
```

Runner từ chối dùng DB khác hoặc cổng đang được dùng, chỉ dừng process do nó tạo. Không tự khởi động/xóa PostgreSQL; từng browser script dọn fixture của mình. `--skip-build` chỉ dùng khi dist đã build đúng mã đang kiểm tra. Redis không được bật trong runner để kiểm tra đường PostgreSQL fallback.

Kiểm tra mobile đo vùng nội dung và vùng nút riêng ở 320/390px, xác nhận không đè lên nhau hoặc làm ký hiệu reaction tràn nút.

Các test mới bao gồm catalog Unicode/picker, retry đồng thời, hai actor/two-source DM, rate sau thời gian chờ, cap, audience đội cũ/người mới/DM, private cursor không đổi, read/unread, rollback, queued kick/finish, mute/pause/dead/postgame và mất ACK/đổi ý/đổi kênh ở browser.

## Kiểm chứng

| Phạm vi | Kết quả |
| --- | --- |
| Toàn bộ backend | **89 test đạt**, gồm 11 ca reaction mới |
| Frontend | **5 file test đạt**, gồm merge reaction không đổi thứ tự tin |
| Browser reaction | **11 nhóm đạt**, không có page error; mobile 320/390px |
| Hồi quy read / realtime / registry-cursor / chat / khung UX2 | **67 nhóm đạt** (10 + 11 + 15 + 17 + 14) |
| Migration 0032 trên DB riêng | **8 kiểm tra đạt**, DB migration được xóa sau kiểm thử |
| Build/schema | TypeScript + Vite đạt; Alembic check không có thay đổi mới |

Tổng cộng **78 nhóm browser** đạt. Sau chỉnh sửa cuối về clock xử lý/rate và giao diện mobile, chạy lại backend/build/schema và riêng 11 nhóm reaction; 67 nhóm hồi quy đã chạy trên lượt triển khai này trước các chỉnh sửa riêng reaction đó.

Artifacts: [browser reaction](reaction-browser-results.json), [migration](reaction-migration-results.json), [mobile](screenshots/reactions-host-mobile.png), [sau trận](screenshots/reactions-postgame.png).

## Phạm vi còn lại

Reaction cho Thông báo chờ UX4; Vote và request trạng thái không nhận reaction theo thiết kế. Chưa làm virtualization 10.000 tin, restore anchor đầy đủ, request item trong DM hoặc ngừng các route numeric legacy. Audit metadata và cổng F/G còn mở. Chưa đo lại 500 người trên toàn luồng mới sau khi thêm reaction; không dùng kết quả tải của kiến trúc cũ làm bằng chứng cho lượt này.
