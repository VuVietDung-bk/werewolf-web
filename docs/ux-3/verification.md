# UX3 · Lượt 1 — timeline, composer, emoji và vị trí đọc

Trạng thái hiện tại: [UX3 hoàn thành](completion.md).

Cập nhật tiếp theo: [lượt 2 đã hoàn thành UX3.2 — DM hợp nhất](unified-dm.md); [lượt 8 đã hoàn thành UX3.6 — reaction cho chat](reactions.md). [Lượt 9](virtual-history.md) đã hoàn thành UX3.7/8. Những mục bên dưới ghi phạm vi và kết quả lịch sử của lượt 1.

Ngày 2026-11-19. Đây là phần đầu của UX3, áp dụng vào ứng dụng thật sau UX2. Toàn bộ UX3 vẫn đang triển khai; reaction, hợp nhất nguồn dữ liệu, virtualize và thay cursor/realtime chưa hoàn thành.

## Hành vi đã triển khai

- Tách `MessageTimeline`, `MessageRow`, `MessageComposer`, `EmojiPicker` và các hàm chuyển dữ liệu/trạng thái khỏi `ChatPanel`. Channel list/member list giữ ở `RoomLayout` của UX2. Phân trang HTTP hiện có được giữ lại.
- Tin liền nhau cùng tác giả/nguồn trong tối đa 5 phút được gom tên/avatar; đổi tác giả, ngày, pha hoặc nguồn sẽ ngắt nhóm. Tin nhiều dòng và tên dài vẫn hiển thị được. Giờ của hàng đã gom và menu hiện khi hover/focus; thiết bị cảm ứng hiện nút thao tác trực tiếp.
- Mốc ngày/pha lấy theo tin gốc, không gắn lại pha hiện tại của phòng. API chỉ bổ sung `phase_type` và `round_number` từ sự kiện gốc của tin đã được phép đọc; không trả payload sự kiện/recipient list. Không cần migration mới.
- Composer nằm cuối vùng Chat; Enter gửi, Shift+Enter xuống dòng, IME đang compose không gửi. Textarea cao tối đa 120px rồi cuộn trong ô; bộ đếm và khóa gửi giữ giới hạn 2.000 ký tự.
- Nháp lưu bằng sessionStorage, khóa gồm tài khoản/phòng/kênh/đội/người nhận. Đổi kênh, sang Role rồi quay lại hoặc reload vẫn giữ nháp của từng kênh. Tab Chat hiện quay về general theo điều hướng UX2; mở lại DM sẽ thấy nháp DM. Lưu trữ bị chặn thì dùng bộ nhớ trong trang.
- Tin gửi lỗi giữ nguyên nháp. ID yêu cầu chưa được xác nhận được giữ qua reload; mất ACK sau khi API đã lưu rồi gửi lại vẫn là cùng lệnh. ACK đến muộn không xóa nháp mới được gõ trong lúc chờ gửi.
- Logout, đổi tài khoản demo và khi mất quyền vào phòng đều dọn nháp/ID gửi chờ và vị trí đọc trong session. Chỉ nháp chưa gửi/ID chờ dùng sessionStorage; cache nội dung tin nằm trong bộ nhớ của ChatPanel.
- Picker có 50 emoji Unicode, tìm tên tiếng Việt có/không dấu hoặc alias, gần dùng, phím mũi tên/Home/End/Tab/Escape. Chèn đúng vị trí con trỏ, trả focus về ô nhập, hỗ trợ emoji ghép. Alias đã biết đổi thành Unicode khi gửi; alias lạ giữ nguyên. Nội dung luôn render dưới dạng text, không dùng HTML từ tin nhắn/CDN.
- Khi tải trang cũ, giữ vị trí của tin đang đọc. Tin đến chỉ tự cuộn khi gần cuối (80px); đang đọc lịch sử có nút “tin mới / Đến cuối”. Vừa gửi tin thì cuộn tới tin mới. Đổi kênh trong cùng ChatPanel khôi phục vị trí và các trang đã tải; ResizeObserver giữ anchor khi vùng đọc đổi kích thước.
- Khóa gửi theo quyền/pha/tạm dừng/mute/role vẫn dựa vào API. Menu sửa/xóa dùng API revision cũ, giữ confirm xóa và lịch sử sau trận. Response sửa hoặc tải tin cũ đến sau khi đổi kênh không nhập vào kênh mới. Khi kết thúc, làm mới cache để hiện bản gốc/các lần sửa/xóa ngay, không cần reload.

## Kiểm chứng

Frontend production build; Chromium local; API port 8026, preview 4176; PostgreSQL **14 riêng** port 55432/database `ma_soi_ux2`, migration head `0027_vote_countdown`. Script từ chối DB ngoài tên/cổng này, tạo tài khoản/phòng giả và dọn đúng ID trong finally. 65 tin lịch sử được tạo qua API; chỉ timestamp của fixture được chỉnh trong DB để kiểm tra nhóm/phân trang mà không phải chờ rate limit. Không dùng DB phòng thật, không tắt giới hạn của ứng dụng.

| Kiểm tra | Kết quả |
| --- | --- |
| `npm run build --prefix frontend` | TypeScript và Vite đạt |
| `npm test --prefix frontend` | Hai file kiểm tra đạt: navigation và presentation/emoji/pending ID |
| `pytest backend/tests/test_chat.py backend/tests/test_role_requests.py backend/tests/test_room_replay.py backend/tests/test_content_revisions.py -q` | 5 passed |
| `scripts/check_ux3.py` | 17 nhóm hành vi đạt, không có page error; [kết quả](browser-results.json) |
| `scripts/check_ux2.py` | Chạy lại regression khung ba tab/luồng phòng; [kết quả UX2](../ux-2/browser-results.json) |
| `git diff --check` | Đạt |

Các nhóm UX3 kiểm tra: trang đầu 30 tin/gom tác giả/mốc pha; prepend giữ anchor; đọc lịch sử khi có tin mới; đổi kênh giữ vị trí; nút đến cuối; Enter/Shift+Enter/IME; nháp qua workspace/reload và tài khoản khác cùng dữ liệu storage; emoji tiếng Việt/alias/ghép/gần dùng/focus trap; chuỗi HTML literal; mất ACK và retry qua reload không nhân đôi; ACK không xóa nháp gõ tiếp; giữ pha gốc; viewport 1440×900, 390×844 và 320×480; menu sửa/xóa và response kín tới trễ; transcript gồm đúng một bộ ba bản gốc/sửa/xóa khi kết thúc; logout dọn nháp.

Ảnh: [general desktop](screenshots/chat-general-1440.png), [picker](screenshots/emoji-picker.png), [DM desktop](screenshots/chat-1440.png), [390px](screenshots/chat-390.png), [320px/chiều cao bàn phím](screenshots/chat-320.png). Emoji dùng font có sẵn trên thiết bị nên hình dạng/màu có thể khác giữa máy.

## Phần còn lại của UX3

- **UX3.1/UX3.2:** phần DM hai nguồn, host/lịch sử sau trận đã hoàn thành ở [lượt 2](unified-dm.md). Registry kênh UUID và endpoint v2 tiếp tục ở lượt sau.
- **UX3.6:** reaction có persistence, quyền, giới hạn, idempotency và lịch sử. Chưa thêm reaction giả trên UI.
- **UX3.7/UX3.8:** vị trí khi quay lại sau khi ChatPanel unmount/reload cần tải lại trang chứa anchor. Hiện chỉ giữ chắc vị trí giữa các kênh có cache trong cùng mount (tối đa 8 kênh); chưa giữ toàn bộ lịch sử qua workspace. Map vị trí chỉ giữ metadata, tối đa 128 mục. Chưa giới hạn DOM bằng virtualization hay đo 10.000 tin.
- **UX3.9:** thay polling/socket/cursor hiện có bằng luồng chung và opaque cursor theo quyền. Lượt này không đóng các mục metadata/cursor còn lại của audit.
- **UX3.10/UX3.11:** trạng thái đồng bộ/quyền chi tiết thống nhất với registry mới và đưa role request vào DM. Request vẫn mở bằng nút/hộp thoại có sẵn.
- Thông báo/Vote vẫn dùng nội dung hiện có, sẽ thay ở UX4. Chưa đánh dấu Kiểm tra F/G hoặc tải 500 người cho UI mới. Kiểm tra viewport/IME mô phỏng không thay lượt thử trên bàn phím/thiết bị thật.

## Chạy lại

Chuẩn bị DB thử độc lập ở `127.0.0.1:55432/ma_soi_ux2`, áp dụng migration. Chạy API ở 8026 với cùng `DATABASE_URL`, `APP_ENV=local`, `APP_ORIGINS=http://127.0.0.1:4176`; preview ở 4176 với `API_PROXY_TARGET=http://127.0.0.1:8026`. Build frontend rồi chạy:

```bash
npm test --prefix frontend
# Terminal dùng DATABASE_URL của DB thử:
.venv/bin/python scripts/check_ux3.py
```

`UX2_CHROMIUM` có thể chỉ định executable Chromium khác. Cần có Playwright Python và Chromium cài sẵn. Thử nghiệm này không cần bật demo mode.
