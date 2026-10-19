# Bước 5 · bàn điều khiển quản trò

## 5.1 · Điều khiển pha và kết thúc trận

- Quản trò chuyển pha thủ công theo Ngày 0 → Đêm 1 → Ngày 1 → Đêm 2. Mỗi lệnh gửi pha và số vòng đang thấy; màn hình cũ bị từ chối để tránh hai lệnh đồng thời làm nhảy hai pha.
- Quản trò tạm dừng với lý do, tiếp tục mà không đổi pha, và kết thúc bằng kết quả cùng giải thích. Kết thúc có bước xác nhận trên giao diện. Người chơi thấy trạng thái tạm dừng và kết quả sau khi kết thúc.
- Các lệnh chỉ dành cho quản trò. API khóa hàng phòng, ghi `game_events` và `outbox` trong cùng giao dịch với thay đổi trạng thái. Khi tạm dừng, không chuyển pha; ràng buộc quyền gửi chat/yêu cầu trong trạng thái này sẽ được thực hiện ở bước 6–7.
- Migration `0008_game_flow` thêm cờ tạm dừng, kết quả và thời điểm kết thúc. Màn trận vẫn có trong thanh điều hướng sau khi kết thúc.

**Kiểm tra:** `alembic check` không báo chênh lệch, `pytest -q backend/tests` đạt **12 passed**, `npm run build` đạt. Test tích hợp xác nhận quyền host, lý do rỗng, tạm dừng/tiếp tục, hai lệnh chuyển cùng pha, chu kỳ Ngày 0 → Đêm 1 → Ngày 1, kết quả thủ công, chặn thao tác sau kết thúc và đủ cặp sự kiện/outbox. Chromium xác nhận quản trò thao tác qua màn trận, người chơi thấy tạm dừng và kết quả sau tải lại ở màn 390 px, không tràn ngang. Dữ liệu kiểm tra đã được dọn khỏi DB local.

## 5.2a · Trạng thái kín và thao tác đầu tiên trên người chơi

- Tab **Bàn quản trò** chỉ xuất hiện cho chủ phòng khi trận đang chơi hoặc đã kết thúc. Bảng hiển thị role, phe, trạng thái sống/chết, hiệu ứng và số ghi chú của từng người; có lọc sống/chết. Chọn một người mở hộp thao tác, giữ màn trận gọn.
- Quản trò có thể giết, hồi sinh, thêm/gỡ hiệu ứng và ghi chú. Giết/hồi sinh cần xác nhận; mọi thao tác trừ ghi chú cần lý do. Trạng thái kín nằm trong `player_states`; ghi chú nằm trong `player_notes`. Cả hai bảng được thêm ở migration `0009_player_state`; người chơi trong trận cũ được đưa vào bảng khi nâng migration.
- Endpoint bảng/ghi chú chỉ trả dữ liệu cho quản trò. `GET /api/rooms/{id}` và danh sách ghế không chứa trạng thái chết hay hiệu ứng chưa công bố. Các thao tác ghi `game_events`/`outbox` trong giao dịch thay đổi trạng thái. `client_request_id` chặn gửi lặp cùng thao tác; giết hai lần, hồi sinh người đang sống và hiệu ứng trùng bị từ chối. Khi tạm dừng, quản trò vẫn ghi chú được nhưng không đổi trạng thái.
- Kiểm tra tích hợp trên PostgreSQL xác nhận quyền host, dữ liệu kín, gửi lặp, trạng thái sau giết/hồi sinh/thêm/gỡ hiệu ứng, ghi chú và cặp sự kiện/outbox. Chromium ở 390 px xác nhận tab kín, hộp thao tác, dữ liệu giữ sau tải lại và không tràn ngang. Phòng/tài khoản thử đã được xóa khỏi DB local.

## Tiếp theo

5.2b: công bố chết/hồi sinh tách khỏi quyết định kín. Sau đó làm 5.2c: đổi role/phe/đội chat và thông tin riêng.
