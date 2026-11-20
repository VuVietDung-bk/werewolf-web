# UX2 · Khung phòng ba tab và Kiểm tra E

Ngày 2026-11-19. UX1 đã được chủ dự án duyệt. UX2 đưa khung mới vào ứng dụng thật; các màn chức năng hiện có được dùng trong khung này. [Thiết kế tham chiếu](../ux-1/README.md).

## Những gì đã thay đổi

- `RoomLayout` quản lý ba tab **Chat / Role / Quản lý**, menu tên phòng, sidebar, header kênh và danh sách người chơi. Trang vào làng có layout riêng; không còn thanh ngang gồm hơn mười tab.
- Thông báo và Vote nằm đầu danh sách Chat, tiếp theo general, đội được server cấp quyền và từng cuộc trò chuyện với quản trò. Trước trận hai kênh đặc biệt có trạng thái rỗng; chức năng đăng thông báo từ sảnh sẽ được triển khai ở UX4.
- Role chứa bộ role/danh mục, phát/chọn và role của mình. Màn chọn role của người chơi được giữ khi đang drafting; sau khi phát đủ, quản trò vẫn có nút bắt đầu Ngày 0. Trong trận không còn mục Phát role.
- Quản lý chứa thông tin sảnh/phòng, bàn người chơi, yêu cầu, điều hành và nhật ký/kết quả/ván mới sau trận. Chức năng host không hiện cho player khi nhập link trực tiếp.
- Chat dùng chiều cao viewport, vùng tin cuộn riêng, composer ở cuối. Sidebar/member cuộn độc lập. Màn <1200px chuyển thành viên sang drawer; <760px chuyển cả danh sách kênh sang drawer. Tab ở 320px không xuống dòng; drawer giữ focus Tab/Shift+Tab, Escape đóng và trả focus.
- Click thành viên mở drawer thông tin **đã công bố**, với đường tới công cụ host/kick khi hợp lệ. Bảng thao tác quản trò đầy đủ và tối ưu drawer thuộc UX6.
- API đọc chat đội cho nhận `team_label` để thu hẹp kết quả đã lọc bằng recipient snapshot. Tên đội không cấp quyền đọc; người ngoài đội vẫn không nhận tin. Gửi đội vẫn do backend quyết định từ role hiện tại.
- Khi đổi kênh trong lúc gửi DM, response muộn của DM không được nhập vào timeline general. Nháp tách theo phạm vi trong bộ nhớ khi ChatPanel còn mount; lưu nháp qua đổi workspace/reload thuộc UX3.
- Glyph kênh, StatusGlyph và role assets dùng lại; skeleton của lazy screen không yêu cầu ảnh nền. Body của room không cuộn, nền/thẻ trang trí lớn của màn cũ không bao quanh khung Chat.

## Kiểm chứng

Môi trường: frontend production build, Chromium, FastAPI local port 8026, preview port 4176, PostgreSQL **14** riêng ở port 55432/database `ma_soi_ux2`, schema lên `0027_vote_countdown`. Không dùng DB trò chơi đang chạy; script chỉ tạo/xóa phòng và tài khoản giả của chính lượt kiểm tra. Không chạy Redis cho lượt này, dùng đường polling PostgreSQL có sẵn. Đây chưa phải phép đo tải/staging PostgreSQL 16.

- `npm run build --prefix frontend`: TypeScript và Vite build đạt.
- `npm test --prefix frontend`: bộ kiểm tra navigation đạt; bao gồm round-trip team/DM, legacy hash, malformed encoding, quyền màn host, draft player, assignment/history và hội tụ route qua sáu trạng thái.
- `pytest backend/tests/test_chat.py backend/tests/test_role_requests.py backend/tests/test_room_replay.py -q`: **4 passed**. Regression mới kiểm tra lọc team chỉ thu hẹp recipient scope, không cho người Làng đọc tin Sói hoặc tên đội không tồn tại.
- `scripts/check_ux2.py`: **14 nhóm kiểm tra trình duyệt đạt**, không có page error. [Kết quả JSON](browser-results.json).
- `git diff --check`: đạt.

| Cổng E | Chứng cứ |
| --- | --- |
| E.1 — Luồng phòng/role/trận | Tạo và vào phòng bằng API thường; trên UI gửi chat sảnh → đóng đăng ký → phát ngẫu nhiên → confirm bắt đầu Ngày 0; kết thúc bằng API → UI kết quả/nhật ký → confirm mở ván mới. Ván mới mở draft bằng API, người đến lượt mở link Role của tôi và chọn thành công trên UI. Ba tab giữ nguyên. |
| E.2 — Link/reload/quyền | `#dead` và `#vote` đổi sang route mới; reload đúng kênh, Back/Forward đúng workspace. Player nhập đường quản trị được chuyển tới màn hợp quyền; Sói xem được đội, Làng không có kênh đó, API thử lọc trái đội trả rỗng. |
| E.3 — Viewport/focus/cuộn | 1440×900, 1024×768, 390×844, 320×740: body không tràn ngang/dọc, composer trong viewport. Drawer Tab nhiều vòng không thoát, Escape trả focus; giảm viewport 390×480 mô phỏng bàn phím, xoay 844×390 vẫn giữ composer. |
| E.4 — Thông tin kín | Giết kín không đổi roster công khai. Đổi role kín và ghi chú của một người không đổi roster/tên kênh của người chơi khác; không có ghi chú kín trong shell người đó. Member sidebar chỉ lấy `public_is_alive/public_role_name`. |

Ảnh: [1440px](screenshots/host-chat-1440.png), [1024px](screenshots/host-chat-1024.png), [390px](screenshots/host-chat-390.png), [320px](screenshots/host-chat-320.png), [chat Sói](screenshots/wolf-team.png), [góc nhìn Làng](screenshots/villager-general.png), [chọn role](screenshots/player-draft.png), [sau trận](screenshots/finished.png).

## Phần tiếp theo

UX2 và E kiểm chứng khung điều hướng và việc giữ các đường thao tác hiện có. Timeline gom tác giả/avatar, emoji picker/reaction, anchor lịch sử/virtualization, unread thống nhất và cursor không lộ metadata nằm ở **UX3**. Vote công khai/bí mật và composer Thông báo tối giản nằm ở **UX4**. Hiện nội dung hai kênh đặc biệt vẫn dùng `VotePanel`/`DeathNotices` cũ; chưa có mode public trong ứng dụng.

Danh sách kênh ở UX2 dùng API context hiện có và quyền server hiện có. Registry kênh, hợp nhất hoàn toàn tin một chiều/request vào timeline cho host và toàn bộ lịch sử sau trận, dùng một luồng realtime/cache, cùng opaque cursor sẽ làm ở UX3. Không coi UX2 đã đóng lỗi metadata của audit.

Kiểm tra mobile dùng viewport Chromium, chưa thay thế một lượt thử với bàn phím/thiết bị thật trong Kiểm tra G. Chưa đo tải 500 người cho layout mới; không kế thừa số đo UI cũ để tuyên bố đạt tải mới.

## Chạy lại

Chuẩn bị DB thử độc lập ở `127.0.0.1:55432/ma_soi_ux2`, áp dụng migration. Chạy API local port 8026 với `APP_ENV=local`, `APP_ORIGINS=http://127.0.0.1:4176` và cùng `DATABASE_URL`; không trỏ vào DB chính. Build frontend, chạy preview port 4176 với `API_PROXY_TARGET=http://127.0.0.1:8026`.

```bash
npm run build --prefix frontend
npm test --prefix frontend
# Terminal đang cấu hình DATABASE_URL của DB thử nói trên:
.venv/bin/python scripts/check_ux2.py
```

Script từ chối DB không đúng tên/cổng local, dọn đúng ID fixture trong finally; ảnh/JSON dùng dữ liệu giả được giữ tại thư mục này. `UX2_CHROMIUM` có thể chọn executable Chromium local khác.

Để chạy lại, chuẩn bị PostgreSQL local riêng ở `127.0.0.1:55432`, DB `ma_soi_ux2` và áp dụng migration hiện có. Terminal API dùng cùng `DATABASE_URL`, `APP_ENV=local`, `APP_ORIGINS=http://127.0.0.1:4176`, bỏ `REDIS_URL` nếu chỉ dùng DB, rồi chạy Uvicorn tại `127.0.0.1:8026`. Build frontend, chạy preview với `API_PROXY_TARGET=http://127.0.0.1:8026 npm run preview --prefix frontend -- --host 127.0.0.1 --port 4176`. Terminal kiểm tra dùng cùng `DATABASE_URL` rồi chạy `.venv/bin/python scripts/check_ux2.py`. Không dùng DB/port của phòng đang chơi.
