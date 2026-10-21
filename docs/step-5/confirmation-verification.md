# Kiểm tra 5.8 · Hộp xác nhận quản trò

Ngày kiểm tra: 20/10/2026. Dùng Chromium headless với một quản trò và hai người chơi trong phòng tạm; phòng và phiên thử đã được xóa sau lượt kiểm tra.

| Thao tác | Hủy | Xác nhận |
| --- | --- | --- |
| Bắt đầu Ngày 0 | Phòng vẫn ở trạng thái đã phát role; phím Escape và nút Hủy đều hoạt động | Bắt đầu đúng một lần; [hộp trên điện thoại](screenshots/confirm-start-mobile-390.png) |
| Chuyển Ngày 0 → Đêm 1 | Vẫn ở Ngày 0 | Chuyển đúng một lần; khi vote còn mở, giao diện chặn trước hộp xác nhận |
| Đăng thông báo tùy chỉnh | Không có thông báo mới | Một thẻ mới xuất hiện cho thành viên; [hộp trên điện thoại](screenshots/confirm-announcement-mobile-390.png) |
| Sửa và xóa thông báo tùy chỉnh | Giữ nội dung/thẻ cũ | Sửa hiện nhãn “Đã sửa”; xóa ẩn thẻ, giữ bản gốc và thao tác trong sự kiện |
| Chốt vote | Không có quyết định | Quyết định được lưu kín, không tự đổi trạng thái người chơi |
| Công bố kết quả vote | Danh sách công khai vẫn trống | Một thẻ kết quả xuất hiện; [hộp trên điện thoại](screenshots/confirm-vote-mobile-390.png) |
| Công bố người chết | Người chơi vẫn được công bố là sống | Trạng thái công khai đổi đúng một lần; nguyên nhân công khai hiện theo bản xem trước |
| Kết thúc trận | Trận tiếp tục | Kết quả và giải thích xuất hiện cho cả phòng đúng một lần |

Hộp dùng chung hiện **pha**, **đối tượng**, **phạm vi** và **nội dung người chơi sẽ thấy ngay**, kèm nút **Hủy**. Với quyết định kín như chốt vote hoặc đánh dấu chết, hộp ghi rõ không có thông báo công khai ngay. Nút Hủy nhận focus khi mở; phím Escape đóng hộp và có thể mở lại ngay. Các ảnh chụp ở chiều rộng 390 px không tràn ngang.

API thông báo tùy chỉnh chỉ cho quản trò đăng; người ngoài không đọc được, người chơi không đăng hoặc sửa/xóa được. Nội dung trống bị từ chối, gửi lại cùng `client_request_id` trả kết quả cũ, dùng mã đó cho nội dung khác bị từ chối. Đăng, sửa và xóa đều có sự kiện/outbox; lỗi ghi sự kiện không tạo thông báo nửa chừng. Không cần migration mới vì dùng bảng sự kiện và bản sửa hiện có.

Kiểm tra hồi quy: `pytest -q backend/tests` **19 passed**; `npm run build`, `alembic check` và `git diff --check` đạt. Phần cấu trúc nâng cao của thông báo tùy chỉnh, gồm loại sự kiện, người liên quan và liên kết với hành động/vote, vẫn thuộc bước 7.5.
