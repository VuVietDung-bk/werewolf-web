# Kiểm tra bước 2.6 và bước 3

Ngày kiểm tra: 2026-10-19. Môi trường local: PostgreSQL 16 trong Docker, FastAPI/Uvicorn, Vite và Chromium headless. Test browser dùng cổng riêng để không đụng các tiến trình dev đang chạy.

## Hành vi phòng

- `pytest -q backend/tests`: 4 test đạt. Các ca quan trọng gồm mã sai, không chấp nhận chính sách, người ngoài xem phòng, người chơi sửa cài đặt, quản trò thử chiếm ghế, vào trùng, đầy phòng, rời phòng và tái sử dụng ghế.
- Hai lệnh tranh ghế cuối chạy đồng thời cho một kết quả 200 và một kết quả 409. Hai ghế vẫn duy nhất và số thứ tự sự kiện/outbox liên tục.
- Sau khi tắt rồi bật lại Uvicorn, ba phiên độc lập vẫn thấy đúng quản trò và hai ghế. Người chơi rời rồi vào lại sau restart thành công.
- Alembic `upgrade head`, `check`, `downgrade -1`, `upgrade head` đều đạt trên DB local; frontend `npm run build` đạt.

## Giao diện

- Ba ngữ cảnh trình duyệt độc lập tạo phòng, vào phòng, tải lại trang và cùng thấy đúng hai người chơi; chỉ quản trò thấy form cài đặt.
- [Sảnh desktop](screenshots/lobby-desktop.png), [điện thoại 390 px](screenshots/lobby-mobile.png), [điện thoại 320 px](screenshots/lobby-narrow-320.png): mã mời, tên người chơi, trạng thái ghế và nút đọc được; không tràn ngang ở 320 px.
- [Trang thử asset desktop](screenshots/asset-demo-desktop.png) và [điện thoại](screenshots/asset-demo-mobile.png): PNG có nền trong suốt hiển thị; ảnh WebP vắng mặt chuyển sang khung chữ dự phòng.
- [Điện thoại khi thiếu nền sảnh](screenshots/lobby-mobile-no-art.png): nội dung và thao tác vẫn rõ. Thử trì hoãn ảnh nền 1,5 giây cũng không chặn giao diện sảnh.

Ở thời điểm kiểm tra bước 3, nền sảnh là PNG khoảng 2,2 MiB. Bước 9.6 đã chuyển sang WebP 351 KiB; xem [báo cáo dung lượng và mạng chậm](../step-9/assets-accessibility.md). Ảnh tại đây là mốc thiết kế, không phải kết quả thử tải 500 người.
