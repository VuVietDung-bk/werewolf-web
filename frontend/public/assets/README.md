# Asset ứng dụng

- Đặt asset tĩnh trong thư mục phiên bản `v1/`, `v2/`... Đường dẫn đã công bố không đổi nội dung; bản sửa có tên mới hoặc phiên bản mới.
- Tên file dùng chữ thường, số và dấu gạch ngang: `<nhóm>-<đối-tượng>-<biến-thể>-<cỡ>.png|webp` (ví dụ `role-seer-night-32.webp`).
- Thư viện `role-*-v1-128.png` có 7 icon nền trong suốt: generic, village, wolf, neutral, seer, hunter, potion. Ảnh gốc do ImageGen tạo theo prompt pixel art Ma Sói huyền bí tối màu, sau đó thu về 128×128 bằng nearest-neighbor để tải nhanh (toàn bộ khoảng 128 KiB). Các bản sửa phải đặt tên mới.
- Dùng PNG hoặc WebP tĩnh; giữ alpha khi có nền trong suốt. `PixelAsset` nhận cả hai định dạng và hiện nhãn khi ảnh vắng mặt hoặc lỗi.
- `v1/seer-concept.png` là bản concept từ `docs/step-1/assets/`, dùng để thử khung ảnh; không dùng làm icon role. Icon quản trò tải lên lưu trong bảng `role_icons` của PostgreSQL, không ở thư mục asset tĩnh.
- `v1/lobby-night-v1.png` là nền sảnh tạo bằng ImageGen từ `docs/step-1/assets/lobby-concept.png`, theo hướng Ma Sói huyền bí tối màu đã chốt. Ảnh có nội dung trang trí; mọi chữ, mã mời, trạng thái ghế và thao tác nằm trong HTML/CSS phía trên.
- Không đặt tên người giữ role hoặc thông tin phân role kín trong URL asset công khai.
