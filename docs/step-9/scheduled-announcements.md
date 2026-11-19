# Hẹn thông báo theo lần chuyển pha kế tiếp

- Trong Bàn quản trò, sau khi đánh dấu chết/hồi sinh, chọn **Khi chuyển ngày/đêm tiếp theo** ở phần xử lý thông báo. Trong popup **Đăng thông báo**, quản trò có cùng lựa chọn cho thông báo công khai tùy chỉnh. Mặc định vẫn là **Đăng ngay**.
- Lịch chờ chỉ quản trò thấy trong tab **Thông báo** và có nút **Hủy lịch**. Quản trò cũng thấy trạng thái **đã hẹn** trên Bàn quản trò. Popup xác nhận chuyển pha liệt kê những thông báo sẽ đăng.
- API ghi quyết định hẹn ở sự kiện kín. Khi lệnh chuyển pha thành công, API đăng thẻ công khai và đổi trạng thái sống/chết công khai trong cùng giao dịch PostgreSQL. Trước thời điểm đó, API phòng, tab Thông báo của người chơi và tín hiệu công khai không lộ nội dung hẹn. Tab đang mở nhận sự kiện WebSocket để tự cập nhật; nhịp đọc phòng định kỳ vẫn dự phòng khi mất kết nối.
- Nếu quản trò đổi trạng thái kín trước khi tới pha kế tiếp, lịch công bố trạng thái cũ bị hủy. Hủy thủ công, kick người được hẹn công bố, hoặc kết thúc trận trước khi chuyển pha cũng hủy lịch và ghi sự kiện. Thông báo đã đăng tiếp tục sửa/xóa theo chức năng hiện có.

Migration: `0026_scheduled_announcements`. Sau khi cập nhật mã nguồn, chạy `alembic -c backend/alembic.ini upgrade head` với `DATABASE_URL` của môi trường cần nâng cấp.

Kiểm tra: 42 test API đạt trên PostgreSQL thử nghiệm; kiểm tra Chromium với hai phiên riêng xác nhận cái chết trong đêm không hiện cho người chơi trước lúc sang Ngày 1, tự hiện sau khi chuyển pha mà không tải lại trang, và thông báo tùy chỉnh có thể hủy trước khi đăng. Migration được nâng/hạ trên DB thử nghiệm và đã áp dụng vào DB local chính ở phiên `0026_scheduled_announcements`.
