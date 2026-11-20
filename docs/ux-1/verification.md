# UX1 · Biên bản kiểm tra bản nháp

Ngày 2026-11-19. Đây là kiểm tra prototype HTML offline và tài liệu; **không phải nghiệm thu E/F/G, phân quyền API, xử lý vote thật hoặc sức tải**.

## Kết quả

Đã chạy `check_wireframes.py` bằng Playwright/Chromium trên file local. Script chặn HTTP/HTTPS, không đăng nhập hoặc gọi API/DB trò chơi. Kết quả: **12 nhóm kiểm tra đạt, không có lỗi JavaScript của trang**. Kết quả có cấu trúc: [browser-results.json](browser-results.json).

| Nhóm | Kết quả quan sát |
| --- | --- |
| Chat desktop 1440 × 900 | Ba cột, đúng ba tab; body không tràn, composer trong viewport. |
| Chat laptop 1024 × 768 | Cột thành viên chuyển drawer, sidebar kênh giữ lại. |
| Chat mobile 390 × 844 và 320 × 740 | Sidebar/member ẩn, mở dialog bằng nút và đóng Escape được; không tràn ngang/cuộn body. |
| Nháp và emoji | Đổi general → Thông báo → general giữ nháp; chọn Unicode emoji chèn vào ô soạn; gửi text hiện thành dòng tin mock. |
| Role editor | Search Sói lọc một role; modal mở được, đổi active/passive không đổi chiều cao mô tả 180px. |
| Player/request drawer | Mở đúng người, xử lý request cập nhật trạng thái mock; có liên kết tác động trong drawer. |
| Vote | Mở vòng secret, đổi góc nhìn player không có host hint; public chỉ hiện phiếu mock sau Chốt, chọn nháp chưa làm đổi timeline. |
| Thông báo | Composer không có reason/kind/target; chọn hẹn không đưa nội dung vào timeline ngay. |
| Sau trận | Không còn ô soạn ở Chat; mở Nhật ký được. |
| Trạng thái phụ | Có mẫu loading/rỗng/offline. |
| Modal mobile | Footer Role còn trong viewport, drawer người chơi mở/đóng được. |

Các số đo layout được ghi trong JSON. Các nhóm layout là bốn ca riêng nên tổng cộng 12 nhóm kiểm tra.

## Ảnh review

| Nội dung | Ảnh |
| --- | --- |
| Chat | [Desktop](screenshots/chat-1440.png) · [Laptop](screenshots/chat-1024.png) · [390px](screenshots/chat-390.png) · [320px](screenshots/chat-320.png) |
| Role | [Sửa role desktop](screenshots/role-editor-1440.png) · [Sửa role mobile](screenshots/role-editor-390.png) · [Chọn 1 trong 3](screenshots/role-draft-1440.png) |
| Quản lý | [Bảng người chơi](screenshots/management-1440.png) · [Drawer desktop](screenshots/player-drawer-1440.png) · [Drawer mobile](screenshots/player-drawer-390.png) · [Xử lý request](screenshots/request-drawer-1440.png) |
| Hai kênh đặc biệt | [Thông báo](screenshots/announcement-1440.png) · [Vote công khai](screenshots/vote-public-1440.png) · [Vote bí mật](screenshots/vote-secret-1440.png) |
| Trạng thái phụ | [Loading](screenshots/chat-loading-1440.png) · [Rỗng](screenshots/chat-empty-1440.png) · [Offline](screenshots/chat-offline-1440.png) · [Kết thúc](screenshots/finished-1440.png) |

## Giới hạn cần giữ khi triển khai

- Quyền trong prototype chỉ đổi dữ liệu minh họa ở client. Ma trận thực phải được test trên server bằng hai phiên, người ngoài phòng, kick và đổi phe.
- Tin, phiếu, request và lịch hẹn là mock trong bộ nhớ. Không có transaction, timer thật, mạng/replay hoặc migration được triển khai trong UX1.
- Scroll vùng/chiều cao đã xem ở trình duyệt desktop giả lập viewport mobile; chưa có bàn phím điện thoại thật, reader thật, anchor tải lịch sử, virtualization 10.000 tin hoặc thử tải 500 người.
- Emoji picker chỉ là bộ mẫu nhỏ, chưa có thư viện tìm kiếm/alias đầy đủ. Nút reaction minh họa bố cục, chưa chứng minh unique/rate hoặc lưu lịch sử.
- Chuyển stage/vai bằng thanh BẢN NHÁP là điều khiển review, không đưa thanh này vào sản phẩm. Giao diện vote ban đầu minh họa public; khi mở vòng mới modal mặc định secret theo đặc tả.
- Các token/cursor/projection và API trong data-contract là thiết kế cho UX3/4, chưa đóng lỗi cursor của audit.

UX1 hoàn tất bộ thiết kế để review và bắt đầu UX2. Chỉ tick các mục UX1 đã có tài liệu/mẫu; UX2–UX6 và E/F/G tiếp tục để trống. Không dùng ảnh của prototype để xác nhận các hành vi backend hoặc thông số production.
