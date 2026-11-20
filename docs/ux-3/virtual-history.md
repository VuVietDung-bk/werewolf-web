# UX3 · Lượt 9 — lịch sử dài và khôi phục vị trí đọc

Ngày 2026-11-20. Tiếp nối [reaction](reactions.md). Phạm vi: UX3.7b và UX3.8 cho chat chung, đội và DM quản trò. Không thay quyền chat, nội dung role hoặc cách quản trò điều hành trận.

Cập nhật tiếp theo: [UX3 đã hoàn thành với request trong DM và đóng đường numeric của player](completion.md). Các số liệu dưới đây thuộc riêng lượt 9.

## Hành vi

- Lịch sử dài chỉ render cửa sổ đang xem và khoảng đệm 600px mỗi phía. Các khoảng chưa render dùng spacer; nhóm tác giả/ngày/pha lấy từ dữ liệu lịch sử đầy đủ, nên không đổi tên hoặc mốc chỉ vì hàng trước bị tháo khỏi DOM. Lịch sử ngắn tối đa 100 entry vẫn render toàn bộ.
- Đo chiều cao hàng thực bằng ResizeObserver, gồm tin nhiều dòng, emoji, reaction và ô sửa. Tin được sửa hoặc có thêm reaction phía trên không kéo người đang đọc xuống cuối. Khi đổi chiều rộng, đo lại và giữ mốc nguồn. Padding/mốc ngày nằm trong mô hình chiều cao, tránh lệch mỗi lần tải trang.
- Hàng có focus hoặc đang sửa được giữ trong DOM dù cuộn ra xa; không mất nội dung ô sửa, picker hoặc vị trí bàn phím. Khi đóng thao tác và chuyển focus, hàng lại theo cửa sổ bình thường.
- Khi đang ở cuối, tiếp tục theo tin mới; đang đọc cũ thì giữ vị trí và có nút đến cuối. Tin chưa render không được ACK là đã đọc. Observer chỉ theo dõi các hàng đã mount, vẫn áp dụng quy tắc viewport/hidden/dialog của lượt 7.
- Mốc đọc lưu theo user/phòng/UUID kênh, gồm source ID, pixel offset và trạng thái theo cuối. Session storage chỉ chứa metadata này, không chứa nội dung tin/cursor/token. Giới hạn 128 mốc; ghi được gộp 150ms và flush khi rời trang/unmount. Logout/đổi tài khoản/mất quyền dọn metadata cùng session chat. Generation của phiên chặn ACK/cleanup đến trễ ghi lại mốc cũ sau khi đã dọn. Storage bị khóa hoặc hỏng thì vẫn dùng bộ nhớ trong phiên.
- Đổi tab Role/Quản lý rồi quay lại hoặc reload sẽ tải một trang bắt đầu từ nguồn đang đọc, giữ pixel offset. Có nút xem tin cũ hơn, xem tin tiếp theo và đến tin mới nhất. Nút xem tiếp có khoảng trống để không bị nút nổi che trên màn nhỏ.
- Mốc đã bị xóa hoặc không còn được phép xem trả lỗi nguồn chung; UI bỏ mốc và tải trang mới nhất. Không quét ngược toàn bộ lịch sử để tìm lại UUID.
- Khi đang ở một đoạn giữa có khoảng trống tới cuối, tin mới ở cuối không được nối ngay sát đoạn cũ. Các chỉnh sửa của tin đã tải vẫn cập nhật. Xem tiếp tải từng trang liên tục; đến cuối hoặc tự gửi tin lấy thẳng trang mới nhất, kể cả khi trang lịch sử đầu chưa tải xong. Khi trang xem tiếp cuối cùng hoàn tất, replay changes từ checkpoint của snapshot để không bỏ sót tin đến trong lúc response đang chờ.

## API và giới hạn dữ liệu

`GET /api/v2/rooms/{room}/channels/{channel}/timeline` nhận đúng một trong:

| Tham số | Ý nghĩa |
| --- | --- |
| Không có hướng | Trang mới nhất |
| `around_item=chat:<uuid>` hoặc `private_message:<uuid>` | Trang bắt đầu tại nguồn được phép xem |
| `before=<opaque token>` | Trang cũ hơn |
| `after=<opaque token>` | Trang tiếp theo |

Response thêm `next_after`; `next_before` và `next_changes` giữ nguyên. Mỗi trang mặc định 30, tối đa 50 item. Token forward ràng buộc user/phòng/kênh/epoch và mục đích `after`, không dùng thay token before/changes. Không trả ordinal, ID event hoặc recipient list. Source lookup dùng chính tập dữ liệu đã áp ACL của timeline, gồm recipient snapshot đội và DM hai nguồn; một UUID không tự cấp quyền. Source đã xóa trong trận không dùng làm mốc; sau trận dùng bản lịch sử theo consent.

Snapshot nguồn và high-water được đọc cùng SQL statement. Snapshot từ mốc giữa đặt creation checkpoint ở high-water được phép xem để changes không phát lại toàn bộ khoảng lịch sử tới cuối như tin mới. Các trang xem tiếp không thay thứ tự server hoặc sort theo UUID/timestamp ở frontend. Khi DB được restore về trước token, API yêu cầu resync.

Không thêm dependency hoặc migration; head vẫn là **0032_message_reactions**. Cần triển khai lại API và frontend. Cache giữ tối đa 8 kênh; tổng 12.000 message được dùng để loại các kênh cũ, riêng kênh đang mở có thể lớn hơn. Virtualization giới hạn DOM, không xóa dữ liệu đã tải khỏi bộ nhớ của kênh hiện tại. Mô hình prefix/nhóm được tính lại khi dữ liệu hoặc chiều cao đổi; thao tác cuộn tìm cửa sổ bằng binary search.

## Sửa truy vấn unread

Lượt 10.000 tin phát hiện truy vấn unread cũ có thể chọn nested loop quét lại cả lịch sử cho từng delivery khi ước lượng số hàng của phòng mới sai sau bulk import. Đã đối chiếu query plan: điều kiện event nằm ở join filter trong khi index scan chỉ lọc room. Ca đo cũ có lần channel discovery mất 49,791 giây; một ca profile query unread mất 33,837 giây.

Truy vấn mới bắt đầu từ các kênh, kiểm tra tồn tại nguồn chưa đọc và tra delivery bằng scalar subquery trên đủ khóa unique `(room, user, source_event)`. Không tạo phép nối lịch sử × delivery. Đo trực tiếp query mới trên cùng fixture là khoảng 18ms; số đo toàn API discovery của lượt cuối ghi trong artifact bên dưới. Quy tắc chỉ tính tin người khác gửi, source chưa xóa, delivery đúng actor và checkpoint riêng được giữ nguyên.

## Kiểm chứng

| Phạm vi | Kết quả |
| --- | --- |
| Backend đầy đủ | **92 test đạt**, gồm 3 ca anchor/forward/ACL mới |
| Frontend | **7 file test đạt**, gồm 6 ca mới cho cửa sổ/chiều cao/metadata và session generation |
| Browser lịch sử dài | **16 nhóm đạt**, không có page error |
| Hồi quy reaction / read / realtime / registry-cursor / chat / UX2 | **78 nhóm đạt** (11 + 10 + 11 + 15 + 17 + 14) |
| Build/schema | TypeScript + Vite đạt; Alembic check không có thay đổi mới, head 0032 |

Tổng cộng **94 nhóm browser** đạt. Sau thay đổi cuối về gửi sớm và generation của mốc đọc, chạy lại build, 7 file frontend, 16 nhóm lịch sử dài và 17 nhóm chat; 61 nhóm hồi quy còn lại đã chạy trước các chỉnh sửa riêng đó. Backend/schema giữ nguyên sau lượt kiểm tra đầy đủ.

Số đo của lượt cuối, Chromium headless trên máy local, một tab chính:

| Chỉ số | Kết quả |
| --- | --- |
| Toàn API discovery, ngay sau bulk seed | **52ms** |
| Tải đủ 10.000 tin qua UI/API | 335 trang, **27,128 giây**; không trang nào quá 50 item |
| Số hàng tin render lớn nhất trong lượt nhảy cuộn desktop | **30** |
| Số hàng tại mốc giữa trên mobile 390px / 320px | **23 / 19** |
| Tổng node DOM sau tải 10.000 tin | **356** |
| JS heap sau GC: đầu trang / đã tải 10.000 tin | **3,16 / 9,32 MiB** |
| JS heap sau GC trên mobile 390px / 320px | **9,33 / 9,40 MiB** |
| p95 thời gian chờ hai animation frame sau mỗi lần nhảy cuộn | **33,8ms** |

Heap là heap JavaScript qua CDP, không phải tổng RAM tiến trình. Phép đo hai frame gồm thời gian chờ lịch trình browser, không phải latency chat hoặc cam kết FPS trên điện thoại thật. Các assertion anchor cho phép sai lệch dưới 3px.

Artifact: [JSON kiểm tra/số đo](virtual-browser-results.json); ảnh [desktop 10.000 tin](screenshots/virtual-10000-desktop.png), [390px](screenshots/virtual-10000-390.png), [320px](screenshots/virtual-10000-320.png).

 Script [check_ux3_virtual.py](../../scripts/check_ux3_virtual.py) chỉ dùng DB disposable localhost:55432/ma_soi_ux2; bulk seed 10.000 nguồn lịch sử cùng event/outbox/delivery trước khi browser kết nối, rồi tải từng trang bằng đường UI/API thật. Tin mới, sửa/xóa, reaction và ACK dùng HTTP thật. Giới hạn ứng dụng và thống kê DB được giữ mặc định.

Lệnh chạy lại với DB kiểm thử đã migration:

```bash
DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:55432/ma_soi_ux2 .venv/bin/python scripts/run_ux3_browser.py --checks virtual
DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:55432/ma_soi_ux2 APP_ENV=local PYTHONPATH=backend .venv/bin/pytest backend/tests -q
npm test --prefix frontend
```

Runner tự build/mở/dừng API và preview; chỉ dọn dữ liệu fixture. Trường hợp virtual có timeout riêng 600 giây để tải toàn bộ lịch sử qua nhiều trang.

## Phạm vi còn lại

Thông báo/Vote và request trạng thái chờ UX4/UX3.11, không dùng renderer này. Chưa ngừng route numeric legacy, chưa đóng audit metadata hoặc cổng F/G. Số đo Chromium headless trên máy local không thay thử trên điện thoại vật lý hoặc thử tải 500 người; UX3 tổng vẫn chưa hoàn thành.
