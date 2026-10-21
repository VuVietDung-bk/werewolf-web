# Kiểm tra B · role và trạng thái trận

Hoàn thành ngày 21/10/2026 trên PostgreSQL local và Chromium headless. Phòng thử và tài khoản thử đã được xóa sau khi kiểm tra.

| Mục | Kết quả | Bằng chứng |
| --- | --- | --- |
| B.1 | Role tùy chỉnh được chốt vào snapshot khi phát; đổi tên, mô tả và icon trong mẫu nguồn sau đó không đổi danh mục hoặc role đã phát. | `backend/tests/test_draft.py`, `backend/tests/test_icons.py`; kịch bản Chromium tải lại danh mục sau khi sửa mẫu nguồn. |
| B.2 | Ngày 0 → Đêm 1; giết/hồi sinh kín rồi công bố, thêm/gỡ hiệu ứng và đổi phe giữ trạng thái API/DB/sự kiện khớp nhau. | `backend/tests/test_game_flow.py`, `backend/tests/test_role_changes_private_messages.py`; [bảng sau đổi role](screenshots/b-control-updated-desktop.png), [ghế công khai trên điện thoại](screenshots/b-public-state-mobile.png). |
| B.3 | Người chơi bị chặn khỏi API quản trò, người ngoài bị chặn khỏi danh mục, người chơi chỉ xem được phân role của mình; danh mục đủ role nhưng không chứa ID người giữ. | `backend/tests/test_roles.py`, `backend/tests/test_draft.py`, `backend/tests/test_icons.py`; [danh mục người 1](screenshots/b-catalog-player-1-mobile.png), [danh mục người 2](screenshots/b-catalog-player-2-mobile.png), [role riêng người 1](screenshots/b-assignment-player-1-mobile.png), [role riêng người 2](screenshots/b-assignment-player-2-mobile.png). |
| B.4 | Gửi lại cùng mã trả kết quả cũ; hai lệnh cùng lúc chỉ một lệnh làm đổi trạng thái; sự kiện/outbox không nhân đôi. | `backend/tests/test_game_flow.py`, `backend/tests/test_draft.py`, `backend/tests/test_role_changes_private_messages.py`. |
| B.5 | Hai màn hình mở đồng thời: người chơi thấy pha đêm sau khi quản trò chuyển pha; giết kín không đổi ghế công khai, công bố chết/hồi sinh mới đổi biểu tượng và nhãn; đổi role cập nhật bảng quản trò mà không lộ role trên ghế công khai. | [bảng quản trò](screenshots/b-control-updated-desktop.png), [màn người chơi](screenshots/b-public-state-mobile.png); kịch bản Chromium khẳng định trạng thái và nội dung. |
| B.6 | Bảy người chọn lần lượt, có lượt người bấm Random và quản trò chọn hộ; kho giảm khi chọn, có lúc còn 1–2 đề cử; lệnh chọn đồng thời không nhân đôi, đủ bảy phân role và bảy sự kiện chọn; không ép số ghế mỗi phe. | `backend/tests/test_draft.py`. |
| B.7 | Icon PNG/JPEG/WebP tĩnh hợp lệ được nhận; icon PNG đã gắn vào role hiện sau tải lại ở hai phiên. Loại sai, ảnh động, quá 1 MiB hoặc vượt 512 px bị từ chối. Icon công khai không tiết lộ người giữ role. | `backend/tests/test_icons.py`; [danh mục người 1](screenshots/b-catalog-player-1-mobile.png), [danh mục người 2](screenshots/b-catalog-player-2-mobile.png). |
| B.8 | Chọn cách phát và bỏ một role khỏi kho, đổi tab rồi tải lại vẫn giữ bản nháp; sau khi bắt đầu Ngày 0, tab sảnh biến mất. Desktop 1280 px và điện thoại 390 px mở/tải lại đúng màn, thân trang không tràn ngang. | [bản nháp phát role](screenshots/b-distribution-draft-desktop.png), [Ngày 0 desktop](screenshots/b-day0-desktop.png), [Ngày 0 điện thoại](screenshots/b-day0-mobile.png). |

## 5.5 · yêu cầu role trên bảng quản trò

`GET /api/rooms/{id}/game/players` trả `pending_request_count` cho từng người, tính các yêu cầu `pending` và `in_review`. Bảng có số tổng và nút lọc “Có yêu cầu chờ”; tự làm mới mỗi 5 giây khi đang mở. Gửi yêu cầu làm số tăng, xử lý xong làm số về 0 và danh sách lọc trống. Quyền đọc bảng vẫn chỉ dành cho quản trò.

[Trước khi xử lý](screenshots/b-pending-request-desktop.png) · [Sau khi xử lý](screenshots/b-request-resolved-desktop.png). Test API trong `backend/tests/test_role_requests.py` kiểm tra cả `in_review`, chấp nhận, từ chối và rút yêu cầu.

## Lệnh kiểm tra

- `pytest -q backend/tests`: **27 passed**.
- `alembic check`: **No new upgrade operations detected**.
- `npm run build`: đạt.
- Kịch bản Chromium/API chạy với hai phiên người chơi và một phiên quản trò; các ảnh trên được chụp từ phòng thử thật.
