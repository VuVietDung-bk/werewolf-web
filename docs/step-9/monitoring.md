# Bước 9.3 · Giám sát và xử lý sự cố

## Tín hiệu vận hành

API xuất Prometheus text tại `GET /internal/metrics` khi có `METRICS_TOKEN` trong môi trường. Gửi `Authorization: Bearer <token>`; thiếu/sai token trả 404. Endpoint không có trong OpenAPI. Chỉ cho Prometheus truy cập qua mạng nội bộ, không đưa token vào URL, repo hay log. Mỗi tiến trình API giữ bộ đếm riêng: nếu chạy nhiều tiến trình, cấu hình Prometheus scrape **từng tiến trình** thay vì đi qua bộ cân bằng tải. Nhãn HTTP là method đã giới hạn, route mẫu và nhóm mã trạng thái; không có ID phòng, tên, nội dung chat hoặc token.

| Nhóm | Số liệu | Ý nghĩa |
| --- | --- | --- |
| HTTP | `ma_soi_http_requests_total`, `ma_soi_http_request_duration_seconds` | Tỷ lệ 5xx và p95 lệnh thay đổi; histogram có bucket 50 ms đến 5 s |
| WebSocket | `ma_soi_websocket_connections`, `ma_soi_websocket_rooms` | Kết nối/phòng đang mở trong tiến trình API |
| PostgreSQL | `ma_soi_postgres_up`, `ma_soi_database_connections`, `ma_soi_database_size_bytes` | Truy vấn DB thành công, số kết nối và dung lượng DB |
| Outbox | `ma_soi_outbox_pending`, `ma_soi_outbox_oldest_age_seconds`, `ma_soi_outbox_retry_pending` | Hàng chưa phát, tuổi hàng cũ nhất và hàng đã thử nhiều lần |
| Redis/worker | `ma_soi_redis_up`, `ma_soi_outbox_worker_heartbeat_age_seconds` | Redis khả dụng và nhịp worker; giá trị `-1` nghĩa là Redis không được cấu hình hoặc chưa có heartbeat |

Worker cập nhật một key Redis có TTL 20 giây khoảng mỗi 5 giây khi vòng xử lý không lỗi. Redis chỉ dùng làm tín hiệu đánh thức; khi Redis lỗi, WebSocket vẫn đọc PostgreSQL định kỳ. Nếu worker dừng và không có outbox mới, heartbeat vẫn giúp phát hiện tình trạng này. Endpoint metrics tiếp tục trả HTTP 200 với `ma_soi_postgres_up 0` hoặc `ma_soi_redis_up 0` nếu một trong hai dịch vụ lỗi, để rule phân biệt lỗi phụ thuộc với lỗi API.

Định dạng text, histogram và thời gian chờ `for` của rule tuân theo [Prometheus exposition format](https://prometheus.io/docs/instrumenting/exposition_formats/), [hướng dẫn histogram](https://prometheus.io/docs/practices/histograms/) và [alerting rules](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/).

## Cấu hình triển khai

- [`prometheus.yml.example`](../../deploy/monitoring/prometheus.yml.example): scrape 15 giây bằng bearer token trong file bảo mật; thay target `api.internal:8000` bằng địa chỉ riêng của từng tiến trình API. Đặt token ngẫu nhiên dài trong `METRICS_TOKEN` của API và file `ma-soi-metrics-token` của Prometheus. Giữ file ngoài repo.
- [`alerts.yml`](../../deploy/monitoring/alerts.yml): API/DB/Redis down, worker mất nhịp, outbox trễ, 5xx, p95 lệnh, WebSocket vượt 550 và DB quá 5 GiB. Ngưỡng 550 kết nối, 500 ms p95, 30 giây outbox và 5 GiB là mốc ban đầu; hiệu chỉnh sau thử tải 9.5 trên hạ tầng thật. Gắn Alertmanager và người trực vận hành ở bước 10.4.
- [`dashboard.json`](../../deploy/monitoring/dashboard.json): import vào Grafana với datasource UID `prometheus`, hiển thị sức khỏe API/DB, kết nối, lỗi, độ trễ, outbox, dung lượng. Dashboard dùng `max` cho số liệu DB/outbox xuất lặp từ nhiều API và `sum` cho kết nối WebSocket từng tiến trình.

Không mở `/internal/metrics` ra Internet. Giám sát riêng mã thoát của cron backup/retention và dung lượng kho backup khi cài lịch ở bước 10.4; metrics API không thay thế cảnh báo backup thất bại. Liveness `/api/health/live` không truy vấn DB, còn `ma_soi_postgres_up` là tín hiệu DB.

## Runbook nhanh

| Cảnh báo | Kiểm tra trước | Xử lý |
| --- | --- | --- |
| `MaSoiApiDown` | Tiến trình API, cổng nội bộ, đường scrape/token | Khởi động lại API nếu đã dừng; xác nhận `/api/health/live` rồi `/internal/metrics` có dữ liệu mới; kiểm tra log lỗi không chứa dữ liệu riêng tư |
| `MaSoiPostgresDown` | PostgreSQL healthcheck, kết nối, dung lượng đĩa | Khôi phục DB/kết nối; không chạy migration khi chưa hiểu lỗi; dùng [runbook backup](backup-restore.md) nếu cần khôi phục |
| `MaSoiRedisDown`, `MaSoiWorkerStale` | Redis `PING`, tiến trình worker, `REDIS_URL` | Khởi động Redis/worker; theo dõi `ma_soi_outbox_oldest_age_seconds` giảm về 0; nếu chỉ Redis lỗi, game vẫn đồng bộ từ DB nhưng có thể chậm hơn |
| `MaSoiOutboxDelayed` | Số hàng chờ, hàng đã retry, worker và Redis | Kiểm tra worker trước; không xóa outbox chưa phát; sau khi hồi phục xác nhận hàng chờ được phát và WebSocket nối lại đủ cursor |
| `MaSoiHttpErrors`, `MaSoiCommandLatency` | Route có 5xx/p95 cao, DB connections, outbox, lượng người chơi | So log theo thời gian và route mẫu; giảm tải hoặc tăng năng lực sau khi xác định nút thắt; chạy lại luồng phòng/vote/chat bị ảnh hưởng |
| `MaSoiConnectionsHigh`, `MaSoiDatabaseLarge` | Số phòng/WS đang mở, kích thước DB, lịch dọn 90 ngày | Đối chiếu chỉ tiêu 500 người; kiểm tra client nối lại liên tục; với DB, kiểm tra retention/backup trước khi tăng dung lượng |

## Kiểm chứng

- `backend/tests/test_observability.py`: token chưa cấu hình/thiếu/sai trả 404; phản hồi chỉ có route mẫu, không có ID phòng/token; PostgreSQL/outbox/Redis/worker metrics có giá trị; DB lỗi vẫn trả `ma_soi_postgres_up 0`; histogram cộng dồn đúng; worker phát heartbeat. Toàn bộ backend **33 passed** sau thay đổi middleware.
- Endpoint trên PostgreSQL/Redis local đang chạy trả `ma_soi_postgres_up 1`, `ma_soi_redis_up 1`, `ma_soi_outbox_pending 132`, DB 11.164.695 byte tại thời điểm diễn tập. Giá trị outbox phản ánh dữ liệu demo đang tồn đọng vì worker local chưa chạy, không phải số liệu tải production.
- `promtool check config prometheus.yml.example` và `promtool check rules alerts.yml` đạt với token giả mount riêng: 9 rule hợp lệ. `promtool test rules alerts.test.yml` đạt: ba cảnh báo PostgreSQL, outbox và worker chuyển sang firing đúng thời gian. Dashboard JSON có 9 panel hợp lệ về cú pháp JSON.
- Khi triển khai staging, scrape API thật và mở dashboard để xác nhận panel có dữ liệu. Hiện repo chưa có hạ tầng production để gắn kênh nhận cảnh báo; đó là bước 10.4.
