# Security audit — ma-soi-bot

## Phạm vi và cách kiểm chứng

- Hồ sơ: **standard**; mã tại commit `2a5f2d6f5262fbbe3c878d28a4736791409ced98` với worktree đang có thay đổi. Các thay đổi tại thời điểm rà soát cũng nằm trong phạm vi đọc. Không có audit có cấu trúc trước đó để kế thừa.
- Phạm vi: FastAPI/backend, React/frontend, cấu hình triển khai mẫu, script bảo trì, CI và phụ thuộc khai báo. `archives/` là mã lịch sử, ngoài đường chạy hiện tại.
- Đây là rà soát mã nguồn. Không chạy ứng dụng, test, build hoặc tải ngoài vì môi trường không cung cấp sandbox hệ điều hành đáp ứng yêu cầu cô lập mạng và tài nguyên của audit. Không kiểm thử hệ thống production.
- Coverage ledger: **18** vùng; 9 đã rà soát không có ứng viên còn mở, 9 vùng liên kết ứng viên, 0 bị chặn, 0 hoãn. Bốn vòng săn tìm và các vòng phản biện sau đó đã kết thúc bằng lượt phản biện cuối sạch. Đây không phải bảo đảm toàn bộ mã không có lỗi.

## Cập nhật mã sau audit · 2026-10-29

Báo cáo bên dưới là ảnh chụp kết luận audit trước khi sửa mã; số dòng có thể dịch chuyển. Sau khi đưa báo cáo vào repo, API chat và yêu cầu role đã đổi `room_cursor` của người chơi thành cursor của dữ liệu họ được xem. Đây là **giảm lộ thông tin**, chưa đóng phát hiện: WebSocket và các trường `sequence` trong bản ghi vẫn dùng số toàn cục. Cần thiết kế cursor opaque hoặc thứ tự theo người nhận xuyên suốt API trước khi coi lỗi này đã xử lý xong.

Đã thêm hạn mức **128 icon và 32 MiB/phòng**, chặn icon sau chuẩn hóa vượt 1 MiB, và chỉ sao chép icon đang được role sử dụng khi mở ván mới. Script retention lặp các lô 100 phòng đến khi hết dữ liệu đủ hạn. Tin nhắn của quản trò có nhãn riêng dựa trên `host_id`. Các chỉnh sửa này chưa được chạy test trong môi trường cô lập của audit; trạng thái xác minh production và các ứng viên khác vẫn ở [NEEDS-VALIDATION.md](NEEDS-VALIDATION.md).

## Tư thế bảo mật nhìn từ mã nguồn

Các kiểm tra phiên, Origin, quyền quản trò/thành viên và phạm vi chat nhìn chung được đặt ở phía server. Phát hiện đã xác nhận là rò rỉ **siêu dữ liệu hoạt động kín** qua cursor toàn cục; không thấy nội dung tin kín bị trả trong đường đi này. Các nguy cơ tài nguyên, giả mạo nhãn UI và tồn đọng lưu trữ cần kiểm chứng thêm trước khi gọi là lỗ hổng.

## Phát hiện đã xác nhận

| Mức độ | Phát hiện | Ranh giới bị ảnh hưởng | Kết quả nhìn thấy |
|---|---|---|---|
| low | A player can infer hidden room activity from global cursors | Cursor sự kiện kín → người chơi trong phòng | Người chơi suy ra hoạt động kín và số sự kiện trong một khoảng quan sát được. |

### Cursor toàn cục làm lộ hoạt động kín

Nguồn: [backend/app/chat.py](../../backend/app/chat.py#L369), [backend/app/db/events.py](../../backend/app/db/events.py#L21), [backend/app/chat.py](../../backend/app/chat.py#L454); thêm [backend/app/live.py](../../backend/app/live.py#L249).
Một người chơi có phiên hợp lệ trong phòng gọi `GET /api/rooms/{room_id}/chat/messages?channel=common` hai lần, trước và sau một sự kiện chỉ quản trò thấy, rồi so sánh `room_cursor` với các sự kiện công khai. Mã nguồn cho thấy `append_event` tăng `Room.version` cho mọi sự kiện, còn API chat trả thẳng giá trị đó. WebSocket ready và trang yêu cầu role cũng dùng cursor toàn cục. Đây là kết quả rút ra trực tiếp từ mã, chưa có request chạy trong sandbox.
Tác động đã chứng minh: biết có hoạt động kín và suy ra số lượng trong khoảng quan sát được; không đọc được nội dung hay danh tính tác nhân từ lỗi này. Mức **low** vì phạm vi chỉ là siêu dữ liệu. Sửa tại điểm trả dữ liệu: dùng cursor theo quyền nhìn thấy của người nhận hoặc token opaque, giữ sequence toàn cục ở server cho replay. Thêm regression test với hai người chơi và một sự kiện chỉ quản trò thấy.

## NEEDS VALIDATION

| Ứng viên | Đường đi nguồn | Chặn kết luận | Bước tiếp theo an toàn |
|---|---|---|---|
| Repeated guest signups can grow persistent identity tables | `backend/app/main.py:169 → backend/app/main.py:176 → backend/app/main.py:179` | Production ingress rate limits and identity cleanup outside this repository are unknown. | With a disposable database and strict request cap, submit a small number of cookie-free requests and compare User and Session counts; inspect cleanup job behavior. |
| A player display name can resemble the moderator label | `backend/app/main.py:72 → backend/app/main.py:179 → frontend/src/components/ChatPanel.tsx:508` | The actual browser styling and contextual indicators were not inspected in an isolated runtime. | In a disposable room, create a player named Quản trò and compare their chat line with a real host message in the rendered UI, including mobile layout. |
| Full transcript export may exhaust worker memory | `backend/app/transcript.py:235 → backend/app/transcript.py:244 → backend/app/transcript.py:251` | Maximum legitimate transcript size and production memory limits are unknown. | On an isolated database, export a small fixed corpus at two bounded sizes while measuring peak worker memory; verify paging or streaming if added. |
| Host icon uploads lack an aggregate storage budget | `backend/app/icons.py:52 → backend/app/icons.py:66 → backend/app/rooms.py:416` | No deployed per-host upload quota, database storage cap, or rate limit is visible. | In a disposable room with a small fixed upload count, measure row and byte growth and confirm whether unreferenced icons are copied. |
| Daily retention can leave a backlog of eligible rooms | `deploy/maintenance.cron.example:10 → scripts/postgres_maintenance.sh:32 → scripts/postgres_maintenance.sh:104` | The production policy may define 90 days as eligibility rather than a maximum deletion deadline. | With disposable data, create 101 eligible rooms and run exactly one retention batch, then a second, checking remaining row counts. |
| A room member may accumulate WebSocket connections | `backend/app/live.py:227 → backend/app/live.py:250 → backend/app/live.py:194 → backend/app/live.py:105` | Production proxy, ASGI and OS connection limits and idle timeouts are unknown. | In an isolated environment with strict socket and time caps, open a small fixed number of idle sockets from one session, compare event fanout counts, then close them. |
| Repeated ballot changes can accumulate host-only events | `backend/app/votes.py:362 → backend/app/votes.py:368 → backend/app/votes.py:412` | The deployed ingress rate limit and vote duration are unknown. | In an isolated test room, submit a small fixed sequence of alternating skip and withdraw choices with new request IDs; compare event and outbox counts. |

## Ghi chú hardening và điểm kiểm soát tốt

- Từ mã nguồn: cookie phiên `HttpOnly`/`SameSite=Lax`, kiểm tra Origin cho write và WebSocket, quyền xem transcript sau ván theo thành viên đã đồng ý, icon được giới hạn từng tệp và chuẩn hóa PNG, nội dung chat React được escape, CSV export có bảo vệ công thức.
- Nếu các ứng viên tài nguyên được xác nhận trong môi trường triển khai, đặt hạn mức tại ingress và ứng dụng theo danh tính/phòng, theo dõi dung lượng và độ trễ, đồng thời giới hạn hoặc stream export. Đây là hướng kiểm tra, chưa phải phát hiện đã xác nhận.
- Cấu hình TLS, reverse proxy, giới hạn kết nối/rate, lịch retention thực chạy, và chính sách xóa công khai không được chứng minh bởi repo; xem [NEEDS-VALIDATION.md](NEEDS-VALIDATION.md) để đối chiếu với chủ vận hành.
