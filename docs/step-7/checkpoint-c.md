# Kiểm tra C · chat, yêu cầu role, vote và thông báo

Nghiệm thu ngày 21/10/2026 trên PostgreSQL local. Phòng và phiên thử được xóa sau mỗi bài test.

| Mục | Kết quả | Bằng chứng |
| --- | --- | --- |
| C.1 | Chat đội theo danh sách người nhận lúc gửi. Người rời đội giữ quyền đọc tin cũ, không đọc tin mới; người mới vào đội không đọc ngược lịch sử. Người chết và người ngoài bị chặn theo chính sách. | `backend/tests/test_chat.py`, `backend/tests/test_live.py`. |
| C.2 | Ngắt WebSocket của hai người, gửi lần lượt chat chung, yêu cầu role và lệnh giết kín. Nối lại từ cursor cũ: người bị giết nhận đủ ba tín hiệu đúng thứ tự, người khác chỉ nhận chat chung; frame không chứa nội dung riêng và không có bản trùng. | `test_reconnect_replays_chat_request_kill_and_vote_publication_in_order` trong `backend/tests/test_live.py`. |
| C.3 | Yêu cầu ở trạng thái đang xét không tự đổi trạng thái người chơi. Quản trò thao tác riêng có lý do, liên kết hành động khi chấp nhận; yêu cầu khác bị từ chối. Người gửi thấy phản hồi, người khác không thấy. | `backend/tests/test_role_requests.py`. |
| C.4 | Giết/hồi sinh kín giữ nguyên ghế và tab Thông báo của người khác. Công bố chết không lộ role nếu không chọn; công bố hồi sinh có thể lộ role. API, danh sách ghế và lịch sử thông báo cùng trạng thái. | `backend/tests/test_game_flow.py`, `backend/tests/test_live.py`; [ảnh giữ kín](screenshots/icons-player-held-mobile.png), [ảnh đã công bố](screenshots/icons-player-published-mobile.png). |
| C.5 | Icon chat, yêu cầu và thông báo có nhãn chữ; các góc nhìn quản trò/người chơi không dùng art để lộ role hoặc cái chết chưa công bố. | [ảnh chat desktop/điện thoại](../step-6/progress.md), [ảnh yêu cầu/thông báo](progress.md). |
| C.6 | Role ngày bị chặn gửi yêu cầu ban đêm, role đêm bị chặn ban ngày, role bị động không tự mở chat quản trò. Quản trò nhắn trước thì chỉ role có `reply_allowed` được đáp. | `backend/tests/test_role_requests.py`, `backend/tests/test_chat.py`. |
| C.7 | Hai thành viên xác nhận chính sách gửi/đọc chat chung ở sảnh, lúc chọn role và Ngày 0; người ngoài và người chưa xác nhận không vào phòng. Đêm mặc định chỉ đọc chat chung. Tin tồn tại sau tải lại và có sự kiện/outbox. | `backend/tests/test_chat.py`, `backend/tests/test_rooms.py`; [biên bản chat](../step-6/progress.md). |
| C.8 | Yêu cầu dạng số, nhiều người và không có mục tiêu giữ nguyên văn bản; quản trò tự xét, server không tạo hiệu ứng hoặc diễn giải luật từ nội dung. | `backend/tests/test_role_requests.py`. |
| C.9 | Trong Ngày 0, quản trò đăng thông báo tấn công khi cái chết còn bị giữ kín, rồi công bố cái chết và đăng sự kiện giết có tên nguồn/ẩn tên nguồn. Trước công bố, người khác vẫn thấy ghế đang sống, tab chết trống và không nhận tín hiệu giết/quyết định kín qua WebSocket. Sau công bố, hai thẻ giết chỉ khác ở nguồn được quản trò chọn hiện. | `test_hidden_or_skipped_death_does_not_leak_through_public_views_or_websocket` trong `backend/tests/test_live.py`, `backend/tests/test_public_announcements.py`. |
| C.10 | Người chơi gửi phiếu, đổi thành bỏ qua rồi rút; mất kết nối và nối lại vẫn thấy phiếu cuối của mình. Quản trò thấy thứ tự ba sự kiện và phiếu chưa gửi. Công bố kết quả rồi sửa: người chơi nhận bản đính chính, còn sự kiện công bố gốc trong DB. | `backend/tests/test_votes.py` và `test_reconnect_replays_chat_request_kill_and_vote_publication_in_order` trong `backend/tests/test_live.py`. |

## Kiểm tra cuối

- `pytest -q backend/tests`: **28 passed**.
- `alembic check`: **No new upgrade operations detected**.
- `npm run build`: đạt.
- `git diff --check`: đạt.

Kiểm tra C xác nhận quyền và tính toàn vẹn dữ liệu. Đo tải 500 người và phục hồi khi tắt Redis/worker vẫn thuộc bước 9 và Kiểm tra D.
