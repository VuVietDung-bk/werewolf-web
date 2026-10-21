# Bước 6 · chat và quyền đội

## 6.1–6.2 · Lưu chat, phân trang và kiểm tra quyền

- Tab **Chat** có chat chung, chat đội và chat với quản trò. Chat chung hoạt động ngay trong sảnh (kể cả khi đóng đăng ký/chọn role), Ngày 0 và các ngày sau. Tin lưu trong PostgreSQL, gắn thứ tự sự kiện và outbox; tải tin cũ theo `before_sequence` (tối đa 50 tin/trang). Trình duyệt lấy tin mới theo `after_sequence`, đi hết các trang còn thiếu và loại trùng theo ID.
- Chat đội chỉ cho người sống có `team_chat_label` gửi ban đêm. Mỗi tin lưu **danh sách người được phép đọc tại lúc gửi**. Người đổi/rời đội hoặc chết vẫn xem tin cũ họ từng được đọc; họ không thấy tin mới. Người mới vào đội không đọc tin trước khi vào. Quản trò xem các đội và gửi vào đội đang có người sống. Phe và đội chat là hai trường độc lập.
- Chat riêng với quản trò là cuộc trò chuyện theo từng người chơi. Role chủ động chỉ mở chat ở pha thuộc `host_contact.initiate_phases`; role bị động không tự mở. Nếu quản trò đã gửi trước, người chơi được trả lời khi role hiện tại bật `reply_allowed`, kể cả tin riêng một chiều đã gửi từ Bàn quản trò trước khi có tab Chat. `reply_allowed: false` chặn trả lời. Khi chết, chỉ role bật `when_dead` mới tự mở chat; trả lời tin quản trò đã mở vẫn theo `reply_allowed`.
- Trong chat chung, người chết chỉ đọc, không gửi; họ vẫn đọc được chat đội cũ từng được phép xem và cuộc trò chuyện riêng của mình. Tạm dừng chặn mọi tin mới. Người ngoài phòng hoặc chưa xác nhận chính sách không truy cập; người chơi không thể chọn cuộc trò chuyện/đội của người khác qua tham số API. Tin chung không lộ role hay danh sách người xem chat đội trong payload công khai. Gửi lại cùng `client_request_id` trả kết quả cũ; cùng mã nhưng khác nội dung bị từ chối.
- Quản trò có thể đổi riêng bốn quyền gửi/nhận: chat chung ban đêm, chat đội ban ngày, người chết gửi chat chung, người chết gửi/nhận chat đội. Mặc định đều tắt. Form yêu cầu lý do, xác nhận trước khi áp dụng, rồi ghi `chat.policy_changed` kèm giá trị trước/sau; mọi phiên đọc chính sách mới từ server. Tạm dừng và quyền mở chat quản trò theo role không bị nới bởi các cờ này.
- API: `GET /api/rooms/{id}/chat/context`, `GET/POST /api/rooms/{id}/chat/messages`, `PATCH /api/rooms/{id}/chat/policy`. `channel=common|team|host`, `before_sequence`, `after_sequence`, `limit` phục vụ phân trang. Quản trò chọn `team_label` hoặc `participant_user_id` để xem kênh tương ứng; người chơi không được chọn tham số này. Migration `0017_chat_messages` tạo bảng/index chính; `0018_chat_lookup_indexes` thêm index cho kênh đội và cuộc trò chuyện riêng; `0019_chat_policy` lưu chính sách phòng.

**Kiểm tra hành vi:** `backend/tests/test_chat.py` chạy qua tin sảnh đang mở/đã đóng, lúc chọn role, phân trang hai hướng, Ngày 0, ngày/đêm, người ngoài phòng, role bị động/chủ động, quyền trả lời có/không, thay đổi đội, người chết, tạm dừng, gửi lặp và đổi chính sách rồi đổi lại. Sau khi ghi tin sảnh, tiến trình Uvicorn được dừng và khởi động lại trên cùng PostgreSQL; tin vẫn được đọc từ phiên người chơi cũ (`API restart smoke: PASS`). Cơ sở dữ liệu thử đã được dọn. Toàn bộ backend **21 passed**, `alembic check` và frontend build đạt.

## 6.3 · Thông báo WebSocket theo quyền

- `WS /api/rooms/{id}/live` xác thực Origin, cookie phiên và quyền vào phòng trước khi kết nối. Mỗi tiến trình API có một tác vụ đọc sự kiện đã commit cho mỗi phòng đang có kết nối; tác vụ chia thông báo cho mọi socket trong phòng sau khi kiểm tra phiên chưa bị thu hồi và thành viên vẫn còn quyền. Nhờ đọc cùng PostgreSQL, sự kiện được thấy dù lệnh HTTP ghi ở tiến trình API khác. `outbox` vẫn giữ nguyên cho worker phát tín hiệu ở bước 9.
- Frame chỉ gồm `type`, `event_id`, `sequence`, `kind`; **không chứa nội dung hoặc payload sự kiện**. Sự kiện công khai tới thành viên phòng; chat đội dùng danh sách người nhận chụp khi gửi; chat riêng và tin quản trò một chiều chỉ tới quản trò/người nhận. Hành động kín trên người chơi chỉ báo cho quản trò và đúng người bị tác động; ghi chú kín chỉ báo quản trò. Đổi đội không làm lộ thông báo của tin đội gửi sau khi rời.
- Màn Chat mở socket qua cùng origin, Vite proxy hỗ trợ nâng cấp WebSocket. Khi có `chat.sent`, màn tải tin được phép đọc bằng API; khi pha/chính sách thay đổi, màn tải lại quyền. Màn hỏi API mỗi 5 giây làm phương án dự phòng khi socket mất.

**Kiểm tra hành vi:** `backend/tests/test_live.py` mở nhiều socket trong một phòng, thử Origin/cookie/người ngoài phòng, tin chung/đội/riêng, đổi đội, ghi chú kín, tin riêng cũ và phiên đã đăng xuất. Người không có quyền không nhận `event_id`; frame được kiểm tra đúng bốn khóa, không có payload. Thử chạy Uvicorn và Vite trên hai cổng local rồi kết nối WebSocket qua Vite proxy từ hai phiên: cả hai nhận cùng sự kiện chat chung (`Vite WebSocket proxy smoke: PASS`). Phòng thử đã xóa. Toàn bộ backend **22 passed**, frontend build và `git diff --check` đạt.

## 6.4 · Nối lại và bù tin

- Mỗi kết nối WebSocket mang cursor riêng (`after_sequence`); máy chủ phát lại các sự kiện đã commit từ đó, kiểm tra quyền với từng người nhận và bỏ qua sự kiện không được xem. Frame `ready` xác nhận cursor bắt đầu. Nhiều socket đang mở không đẩy cursor của phiên vừa nối lại lên sai vị trí.
- Trình duyệt nhớ cursor theo phòng trong `sessionStorage`, tự nối lại với thời gian chờ tăng dần tối đa 10 giây, và giữ hỏi API mỗi 5 giây khi socket mất. Sau khi nối lại, màn tải lại quyền/phòng và lấy các trang chat còn thiếu bằng `after_sequence`; tin được gộp theo ID và việc tải chồng nhau được tuần tự hóa. Sự kiện WebSocket lặp được lọc theo `event_id`; gửi lặp cùng `client_request_id` vẫn trả kết quả cũ.

**Kiểm tra hành vi:** `test_live.py` ngắt socket người chơi, ghi xen sự kiện công khai và sự kiện kín trong khi socket quản trò vẫn mở, rồi nối lại từ cursor cũ. Người chơi nhận đủ sự kiện công khai đúng thứ tự, không nhận sự kiện kín hay bản trùng; cursor không hợp lệ bị từ chối. Frontend build đạt.

## 6.5 · Giới hạn gửi và chặn chat

- Tin nhắn tối đa 2.000 ký tự và không được chỉ có khoảng trắng. Giới hạn mặc định theo người/phòng, cộng cả ba kênh: tối đa 6 tin trong 10 giây và 30 tin trong 60 giây. Vượt giới hạn trả HTTP 429 cùng `Retry-After`; gửi lại đúng mã yêu cầu không tính thành tin mới.
- Quản trò có thể chặn hoặc mở quyền gửi của từng thành viên với lý do bắt buộc. Trạng thái nằm trong `chat_mutes`; mỗi thay đổi ghi `chat.mute_changed` vào journal/outbox, gửi tín hiệu riêng cho quản trò và người bị tác động. Người bị chặn vẫn được đọc những kênh họ có quyền xem; quyền gửi cả ba kênh bị chặn ngay trên server. Giao diện hiện lý do cho đúng người và nút quản lý cho quản trò. Migration `0020_chat_mutes` tạo bảng và index phục vụ giới hạn tần suất.
- Nội dung chat được hiển thị bằng React text node, nên chuỗi HTML do người chơi gửi hiện nguyên văn; giao diện không dùng `innerHTML` cho chat.

**Kiểm tra hành vi:** `test_chat_moderation.py`, `test_chat.py` và `test_live.py` bao phủ spam, gửi lặp, giới hạn độ dài, chuỗi HTML độc hại, chặn/mở chat, quyền đọc còn giữ, ba kênh chat, lý do, outbox và tín hiệu WebSocket riêng. Toàn bộ backend **24 passed**, `alembic check` và frontend build đạt.

## 6.6 · Tab pixel và tin mới

- Ba tab **Chung**, **Đội**, **Quản trò** dùng icon SVG vẽ trên lưới pixel, luôn có nhãn chữ và trạng thái chọn. Tin mới được đánh dấu bằng chữ **Mới** và viền vàng, không chỉ dựa vào màu. API trả số thứ tự tin đến mới nhất theo từng kênh/cuộc trò chuyện; người chơi chỉ nhận số của tin chung, tin đội có mình trong danh sách người nhận lúc gửi và chat riêng của mình. Tin do chính người xem gửi không bật dấu mới.
- Trình duyệt ghi số thứ tự đã xem theo phòng, tài khoản và cuộc trò chuyện trong `sessionStorage`. Quản trò xem một đội hoặc một người chỉ xóa dấu mới của đúng cuộc trò chuyện đó. Dấu mới chỉ biến mất sau khi API tải được tin vào vùng chat. Vùng tin có chiều cao giới hạn, thanh cuộn riêng, ngắt dòng dài và nền/chữ tương phản; phần cài đặt quản trò được thu gọn.
- Ảnh kiểm tra giao diện: [desktop 1280×900](screenshots/chat-desktop.png) và [điện thoại 390×844](screenshots/chat-mobile.png). Cả hai không tràn ngang; ở bản điện thoại, các tên tab vẫn nằm trên một dòng. Phòng dùng để chụp đã được xóa sau kiểm tra.

**Kiểm tra hành vi:** backend kiểm tra số thứ tự tin mới không lộ chat đội/riêng cho người khác, kể cả sau khi đổi đội. Chromium thử hai kích thước, chuyển tab, tải lại trang và xác nhận đúng dấu mới. Toàn bộ backend **24 passed**, frontend build, `alembic check` và `git diff --check` đạt.

**Bước 6 hoàn tất.** Mục tiêu hiển thị ≤2 giây và tải 500 người đồng thời còn cần đo ở bước vận hành.
