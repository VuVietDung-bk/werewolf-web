# Asset ứng dụng

- Đặt asset tĩnh trong thư mục phiên bản `v1/`, `v2/`... Đường dẫn đã công bố không đổi nội dung; bản sửa có tên mới hoặc phiên bản mới.
- Tên file dùng chữ thường, số và dấu gạch ngang: `<nhóm>-<đối-tượng>-<biến-thể>-<cỡ>.png|webp` (ví dụ `role-seer-night-32.webp`).
- Thư viện `role-*-v1-128.png` có 7 icon nền trong suốt: generic, village, wolf, neutral, seer, hunter, potion. Ảnh gốc do ImageGen tạo theo prompt pixel art Ma Sói huyền bí tối màu, sau đó thu về 128×128 bằng nearest-neighbor để tải nhanh (toàn bộ khoảng 128 KiB). Các bản sửa phải đặt tên mới.
- Dùng PNG hoặc WebP tĩnh; giữ alpha khi có nền trong suốt. `PixelAsset` nhận cả hai định dạng và hiện nhãn khi ảnh vắng mặt hoặc lỗi.
- `v1/seer-concept.png` là bản concept từ `docs/step-1/assets/`, dùng để thử khung ảnh; không dùng làm icon role. Icon quản trò tải lên lưu trong bảng `role_icons` của PostgreSQL, không ở thư mục asset tĩnh.
- `v1/lobby-night-v1.png` là nền sảnh tạo bằng ImageGen từ `docs/step-1/assets/lobby-concept.png`, theo hướng Ma Sói huyền bí tối màu đã chốt. Ảnh có nội dung trang trí; mọi chữ, mã mời, trạng thái ghế và thao tác nằm trong HTML/CSS phía trên.
- `v1/village-day-v1.png` là biến thể ban ngày cho màn trận và bàn quản trò, tạo bằng built-in ImageGen từ `v1/lobby-night-v1.png`. Màn đêm dùng chính nền sảnh; lớp phủ tối đặt bằng CSS để chữ dễ đọc. Biểu tượng hiệu ứng/chết/hồi sinh được vẽ theo lưới pixel trong `StatusGlyph.tsx` và luôn đi cùng nhãn chữ.
- Không đặt tên người giữ role hoặc thông tin phân role kín trong URL asset công khai.

Prompt của `village-day-v1.png`:

> Use case: stylized-concept. Asset type: panoramic pixel-art background for the Day phase of a dark mystical Werewolf browser game. Edit the provided village-at-night artwork into the same village at early morning. Preserve the village layout, camera position, buildings, mountains, trees, paths, pixel-art texture and wide composition. Replace the moon with soft sunrise light and a pale gold sky; keep mysterious atmosphere with cool blue shadows and muted amber light. Keep center and lower middle visually quiet so web UI text and player cards can sit over a dark overlay. No characters, lettering, logos, UI, or watermark. This is a daylight variant of the provided image, not a new scene.
