# UX3 · Lượt 4 — timeline HTTP với cursor mã hóa

Ngày 2026-11-19. Tiếp nối [registry kênh](channel-registry.md). Lượt này hoàn thành phần HTTP của UX3.9 và chuyển màn Chat sang timeline v2; UX3.9 còn realtime/delivery và ngừng đường legacy.

Cập nhật 2026-11-20: [lượt 5](recipient-delivery.md) đã bổ sung delivery/HTTP recovery và migration head `0030_room_deliveries`. Các kết quả và hướng dẫn bên dưới ghi phạm vi lượt 4; khi cập nhật mã mới cần migration ở lượt 5.

## Thay đổi trong ứng dụng

- Chat chung, đội và DM dùng một định dạng item có khóa `chat:<uuid>` hoặc `private_message:<uuid>`. API trả tác giả, nội dung, thời gian UTC, quyền sửa/xóa và lịch sử sau trận; không trả số thứ tự hay ID sự kiện toàn phòng trong item/lịch sử v2.
- `GET /api/v2/rooms/{room}/channels/{channel}/timeline` nhận `before` và trả `next_before`, `next_changes`. `GET .../changes` nhận `cursor`, trả thay đổi và cursor tiếp theo. Trang tối đa 50 item.
- Quản trò sửa/xóa theo nguồn qua `POST .../items/{chat|private_message}/{id}/revisions`. API kiểm tra nguồn thuộc đúng phòng/kênh, quyền quản trò và trạng thái trận sau khi lấy khóa phòng; một lệnh chờ khóa không thể sửa sau khi trận đã kết thúc.
- Frontend giữ thứ tự trang server, khử trùng theo nguồn và không sort bằng sequence. Thay đổi của tin chưa tải không tự chèn vào đầu/cuối hội thoại. Tin mới đã sửa hoặc xóa trước lần đồng bộ đầu vẫn được xử lý qua luồng quét nguồn mới riêng với luồng cập nhật.
- Khi đang tải trang cũ, client tạm giữ các sửa/xóa nhận được rồi đối chiếu trước khi chèn trang. Phản hồi từ kênh cũ bị bỏ qua sau khi đổi kênh. Buffer giới hạn 512 nguồn; vượt giới hạn sẽ lấy snapshot mới.
- Cursor hết hạn, bị sửa, sai phạm vi hoặc đi trước trạng thái DB sau restore trả `409 chat_resync_required`. Màn Chat lấy snapshot mới và giữ nháp; lỗi ở nút tải lịch sử cũng phục hồi được.
- Dấu đã đọc trong phiên dùng ID nguồn gần nhất thay cho sequence. Đây vẫn là marker local theo kênh, chưa phải vị trí đã đọc trên server hay xác nhận item thực sự lọt vào viewport.
- Composer mở lại ngay khi server xác nhận gửi thành công; việc tải các trang đồng bộ tiếp tục độc lập. Trường hợp mất ACK vẫn giữ khóa retry để tránh gửi trùng.

## Cursor và cấu hình

Cursor dài cố định 114 ký tự, mã hóa/xác thực bằng AES-256-GCM của thư viện `cryptography`, nonce ngẫu nhiên 96 bit. Associated data gắn token với phòng, người xem, UUID kênh và mục đích (`before`/`changes`). Payload chứa checkpoint và fingerprint phạm vi đọc; đổi đội của người xem hoặc kết thúc trận yêu cầu snapshot mới. Fingerprint không dùng room version hay hoạt động riêng của người khác.

Token sai không được phản chiếu trong thông báo lỗi. Decoder kiểm tra độ dài, bảng ký tự và base64 chuẩn trước khi giải mã. TTL là 1 giờ, cho phép đồng hồ phát token đi trước tối đa 30 giây. Không có thay đổi được phép xem thì `/changes` giữ nguyên token đầu vào. Mỗi lần lấy snapshot ban đầu vẫn sinh token ngẫu nhiên mới, dù dữ liệu giống nhau; không dùng nonce cố định để ép ciphertext giống nhau.

Tham khảo thư viện: [AESGCM và yêu cầu nonce](https://cryptography.io/en/stable/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM), [gói cryptography 50.0.2](https://pypi.org/project/cryptography/50.0.2/). Mã không tự cài thuật toán mã hóa.

Không thêm migration; head vẫn là `0029_room_channels`. Cập nhật dependency và khởi động lại API:

```bash
python -m pip install -e './backend[dev]'
```

Với `APP_ENV=local`, một API worker có thể tự sinh khóa tạm khi không cấu hình. Khởi động lại làm token cũ hết hiệu lực; UI tự lấy snapshot mới. Để chạy nhiều worker hoặc giữ token qua restart, tất cả worker phải dùng cùng key ring bền vững. Môi trường khác local sẽ từ chối khởi động khi thiếu khóa.

Tạo key ring trên máy triển khai rồi lưu đầu ra vào secret store (không commit):

```bash
.venv/bin/python -c 'import base64,secrets,json; print(json.dumps({"chat0001":base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()}))'
```

Đặt `CHAT_CURSOR_KEYS` thành JSON vừa tạo và `CHAT_CURSOR_ACTIVE_KEY=chat0001`. Key ID gồm đúng 8 chữ/số; mỗi khóa là 32 byte mã hóa URL-safe base64. Cho phép tối đa 4 khóa để xoay vòng. Giữ khóa cũ ít nhất 1 giờ + 30 giây sau khi worker cuối cùng ngừng phát token bằng khóa đó; cấu hình chỉ nạp khi khởi động tiến trình. Token không đổi checkpoint có thể giữ key ID cũ đến lúc hết hạn.

API ghi route template và tắt access log Uvicorn theo cấu hình hiện có. Khi triển khai cần cấu hình reverse proxy không ghi query chứa cursor; lượt này chưa cài cấu hình log production của proxy.

## Kiểm chứng

| Phạm vi | Kết quả |
| --- | --- |
| API/cursor và hồi quy chat, quyền, live, transcript | 29 test đạt |
| Frontend: merge/presentation/navigation | 3 file test đạt |
| Build production | TypeScript + Vite đạt |
| [Chromium, API thật và PostgreSQL riêng](cursor-browser-results.json) | 15 nhóm đạt, không có page error |
| [Hồi quy UX3 lượt 1](browser-results.json) | 17 nhóm đạt |
| [Hồi quy UX2](../ux-2/browser-results.json) | 14 nhóm đạt |

Các ca mới gồm: token bị sửa/sai tài khoản/phòng/kênh/mục đích; hết hạn/xoay khóa/cấu hình fail closed; sự kiện kín khác không làm đổi changes token; phân trang DM xen hai nguồn; tin mới bị sửa/xóa trong khi phân trang changes; rollback DB; đổi đội/kick/sau trận; lệnh sửa chờ kết thúc trận; token lỗi phục hồi nháp; phản hồi lịch sử cũ đến sau sửa tin chưa tải.

Lệnh chạy API dùng DB thử nghiệm đã migration, không dùng DB đang chơi:

```bash
DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:55432/ma_soi_ux2 APP_ENV=local PYTHONPATH=backend .venv/bin/pytest backend/tests/test_chat_cursors.py backend/tests/test_chat_timeline_v2.py backend/tests/test_channels.py backend/tests/test_chat_timeline.py backend/tests/test_chat.py backend/tests/test_chat_moderation.py backend/tests/test_content_revisions.py backend/tests/test_role_changes_private_messages.py backend/tests/test_kick.py backend/tests/test_live.py backend/tests/test_role_requests.py backend/tests/test_room_replay.py backend/tests/test_transcript.py -q
npm test --prefix frontend
npm run build --prefix frontend
```

Browser: chạy API cổng 8026, frontend preview 4176 trỏ API đó, sau đó chạy `scripts/check_ux3_channels.py --opaque` bằng Python trong `.venv`. Script giới hạn DB thử nghiệm `ma_soi_ux2` ở localhost:55432 và dọn dữ liệu do nó tạo.

## Phần còn lại

Chưa tạo delivery projection bất biến theo người nhận, vị trí đọc trên server hay một luồng WebSocket phòng dùng chung với cursor opaque. Chat vẫn dùng WS cũ để đánh thức và polling để bù. Các API legacy, chat context, WS, yêu cầu role và thông báo vẫn còn metadata numeric; chưa ngừng hay trả 426 các đường đó. Chứng cứ không lộ ordinal ở trên chỉ áp dụng response timeline/changes v2, không phải toàn API/WS. Chưa đóng vấn đề audit metadata.

Reaction, virtualization/restore anchor với 10.000 tin, request item trong DM, cổng F/G và thử tải mới 500 người vẫn chờ các lượt tiếp theo. Không đánh dấu toàn bộ UX3.1b/UX3.9 hoàn thành.
