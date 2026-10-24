# Bước 9 · Ổn định và vận hành

## 9.1 · Worker outbox và đồng bộ sau gián đoạn

`app.outbox_worker` lấy tối đa 100 hàng outbox chưa phát mỗi lượt, khóa `FOR UPDATE SKIP LOCKED` để nhiều worker không cùng xử lý một hàng. Worker chỉ gửi `room_id` và `sequence` qua Redis Pub/Sub, không gửi nội dung, role hay người nhận. Sau khi `PUBLISH` thành công mới ghi `published_at`; khi lỗi, giữ hàng chờ, tăng `attempts` và thử lại với khoảng nghỉ tối đa 10 giây. Nếu tiến trình chết sau khi Redis nhận tín hiệu nhưng trước khi commit, tín hiệu có thể phát trùng; WebSocket dùng `game_events.sequence` để chỉ gửi từng sự kiện một lần trên mỗi kết nối.

Mỗi tiến trình API có một Redis subscriber cho các phòng đang mở WebSocket. Tín hiệu chỉ đánh thức feed; feed tự đọc các sự kiện đã commit từ PostgreSQL rồi lọc theo quyền người nhận. Feed vẫn tự đọc DB mỗi 0,4 giây khi không có tín hiệu. Vì Redis Pub/Sub [không lưu tín hiệu cho subscriber đang ngắt](https://redis.io/docs/latest/develop/use-cases/pub-sub/), PostgreSQL là nguồn dữ liệu để nối lại. Chat, yêu cầu role, vote, thông báo, tin riêng và trạng thái phòng cũng có đường tải lại qua HTTP khi WebSocket ngắt; chat/yêu cầu dùng cursor và gộp theo ID.

### Kiểm tra

- `backend/tests/test_outbox_worker.py`: worker lỗi phát Redis giả lập thì hàng vẫn chờ và số lần thử tăng; bật lại thì phát đúng `room_id`/`sequence`, đánh dấu đã phát và không phát lại. Hai worker đồng thời dùng `SKIP LOCKED` không lấy trùng. RoomFeed nhận sự kiện từ DB khi worker ngừng, cả khi `REDIS_URL` trỏ tới nơi không kết nối được; đọc lại không nhân đôi frame.
- Kiểm thử toàn phần phát hiện race khi kết nối mới tham gia giữa lúc feed đang đọc DB: feed có thể gửi một sự kiện mới rồi nhảy cursor, bỏ các sự kiện cũ của người mới nối lại. Feed nay chụp danh sách kết nối và cursor cùng thời điểm trước khi truy vấn; kết nối đến giữa truy vấn được xử lý trong lượt sau. Test tái hiện đúng thời điểm này và xác minh phát lại đủ 3 sự kiện theo thứ tự.
- Diễn tập Redis thật trên DB riêng `ma_soi_step9`: tạo sự kiện khi worker chưa chạy, RoomFeed đọc được `room.created`, outbox vẫn chờ với `attempts=0`. Tắt container Redis, chạy worker: hàng vẫn chờ và `attempts` tăng. Bật lại Redis: worker tự ghi `published_at` cho hàng đó mà không khởi động lại. Thử subscriber Redis riêng: tín hiệu đúng phòng đánh thức feed. DB diễn tập được xóa sau kiểm tra.
- Toàn bộ backend **31 passed** sau khi sửa race; ca WebSocket phân quyền cũng qua khi cấu hình `REDIS_URL` nhưng không chạy worker. Frontend build, Alembic check và `git diff --check` đạt.

Lệnh local ở [README](../../README.md). Bước 9.3 sẽ bổ sung dashboard, cảnh báo hàng outbox chờ lâu và cách giám sát worker khi triển khai.

## 9.2 · Sao lưu và khôi phục PostgreSQL

Đã có [runbook và biên bản diễn tập](backup-restore.md) cùng script sao lưu, kiểm tra SHA-256, khôi phục vào DB riêng và xem trước/xóa dữ liệu theo thời hạn. Chủ dự án đã chọn xóa phòng sau 90 ngày và giữ bản sao tối đa 7 ngày; [mẫu cron](../../deploy/maintenance.cron.example) dọn bản sao local lúc 01:00 UTC, sao lưu rồi xóa phòng đủ hạn lúc 02:00 UTC. Cài lịch thật và lifecycle kho ngoài máy ở bước 10.4.

## 9.3 · Metrics, dashboard và cảnh báo

[Tín hiệu, cấu hình và runbook](monitoring.md) đã được thêm cho API, WebSocket, worker, Redis, outbox và PostgreSQL. Bộ rule Prometheus có dữ liệu thử; dashboard Grafana nằm trong `deploy/monitoring/`. Triển khai scrape và người nhận cảnh báo thuộc bước 10.4.

## 9.6 · Asset và khả năng truy cập

[Báo cáo 9.6](assets-accessibility.md) ghi dung lượng trước/sau, các request thực khi mở từng màn, ảnh desktop/điện thoại, tương phản, alt/fallback, giảm chuyển động và thời gian tải trong kết nối chậm giả lập. Mẫu header cache nằm ở `deploy/nginx-static-cache.conf.example`; kiểm tra trên proxy thật thuộc bước 10.4.
