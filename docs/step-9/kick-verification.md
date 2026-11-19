# Kiểm tra quản trò kick người chơi · 27/10/2026

- `POST /api/rooms/{id}/players/{player_id}/kick` chỉ cho quản trò, cần lý do; gửi lại cùng `client_request_id` không tạo sự kiện thứ hai. Kick làm ghế trống và thu hồi quyền đọc/gửi trong trận. Sự kiện `member.kicked` ghi người bị kick, ghế và lý do trong journal; người từng tham gia vẫn xem nhật ký sau trận.
- Kick ở trạng thái `assigned` bỏ bản phân role của người bị loại để trận còn lại bắt đầu được. Khi đang chọn role, hệ thống bỏ lượt của người bị loại, trả role đã chọn về kho và mở lời mời cho lượt kế tiếp nếu cần. Vote đang mở phải đóng trước khi kick để không thay đổi phiếu khi vẫn nhận vote. Sau khi mở ván kế tiếp, quản trò phải kick ở sảnh ván mới.
- Kick là thao tác loại khỏi phòng hiện tại; nếu đăng ký còn mở, người bị kick có thể dùng mã mời để vào lại. Giao diện nhắc điều này trước khi quản trò xác nhận.

**Test:** `test_kick.py` kiểm tra quyền, lý do, gửi lặp, ghế và bản phân role, draft đang/chưa chọn, vote đang mở, quyền truy cập của người bị kick, thao tác quản trò trên người đã rời, trận vẫn bắt đầu được và nhật ký sau trận. Toàn bộ backend **39 passed** trên DB thử riêng; frontend build và `git diff --check` đạt. [Kịch bản Chromium](kick-browser.json) kiểm tra nút trong sảnh, hai bước xác nhận, ghế trống, chuyển người bị kick về trang chủ và không có lỗi JavaScript.
