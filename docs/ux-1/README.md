# UX1 · Bản nháp ba tab

Mở [wireframes.html](wireframes.html) trực tiếp bằng trình duyệt, không cần chạy API. Chọn Vai và Ván ở thanh trên cùng để xem quản trò, người Làng hoặc Sói trong sảnh, khi chọn role, đã phát role, trong trận và sau trận. Thanh này chỉ phục vụ bản nháp.

Bấm ba tab Chat / Role / Quản lý. Trong Chat, chọn kênh ở trái; trên điện thoại dùng nút ☰ và nút thành viên. Trong Role, chọn Vai Quản trò + Ván Sảnh để thử modal sửa và số bản, hoặc Người Làng + Chọn role để xem lời mời. Trong Quản lý, thử mở người chơi, hàng đợi yêu cầu, cài đặt và nhật ký.

- [Đặc tả hành vi, điều hướng, scroll và tokens](product-spec.md)
- [Schema, API đề xuất, quyền, cursor và migration vote](data-contract.md)
- [Ảnh và biên bản kiểm tra](verification.md)

Mẫu trạng thái phụ có thể mở bằng `?state=loading#chat/general`, `?state=empty#chat/general`, `?state=offline#chat/general`. Ví dụ vai/trạng thái: `?actor=player&stage=draft#role/mine`.

Script kiểm tra prototype dùng Python Playwright và Chromium local đã có trong môi trường phát triển: `.venv/bin/python docs/ux-1/check_wireframes.py`. Nếu Chromium của bạn ở đường dẫn khác, đặt `UX1_CHROMIUM` về executable đó. Script chỉ mở file trong thư mục này và chặn HTTP/HTTPS. Nó ghi lại ảnh trong `screenshots/` và kết quả trong `browser-results.json`.

Đây là mẫu tương tác dùng dữ liệu giả; lưu/gửi không tác động ván thật. Bản thiết kế tiếp tục được chỉnh theo phản hồi trước khi thay giao diện sản phẩm.
