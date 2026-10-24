# Bước 9.5 · Thử tải 500 người chơi

## Kịch bản và cách đo

Script [`scripts/load_step_9_5.py`](../../scripts/load_step_9_5.py) tạo **20 phòng × 25 người chơi** trong DB `ma_soi_load` riêng. Các phòng được seed ở Ngày 0 với role hợp lệ; API được dùng để mở vòng vote, gửi chat, bỏ phiếu và đăng thông báo. Mỗi người chơi giữ một WebSocket; tổng 500 kết nối. Sau khi kết nối ổn định, script gửi **25 tin chat/giây trong 600 giây** (15.000 tin) và ở giữa bài phát **25 phiếu vote + 25 thông báo quản trò trong một giây**. Một ca phụ phát cả 50 lệnh cùng một thời điểm để thấy giới hạn khi dồn tải mạnh hơn.

Độ trễ chat tính từ lúc client bắt đầu POST đến lúc từng WebSocket người nhận nhận được `event_id`; thống kê cả **375.000 lượt nhận** và thời điểm người nhận cuối của từng tin. Độ trễ lệnh quản trò tính từ lúc POST đến phản hồi HTTP. Kết thúc bài, script đối chiếu ID chat được HTTP xác nhận với `chat_messages` trong PostgreSQL, số frame đến đủ 25 ghế và số outbox với sự kiện. Các mã yêu cầu là UUID riêng; mọi tin dùng kênh chung Ngày 0 nên không bị giới hạn chat theo người gửi.

### Môi trường local

- WSL2 Linux, 20 CPU logic được báo bởi hệ điều hành, RAM 7,6 GiB hiển thị khi đo. API chạy **một** tiến trình Uvicorn `--ws websockets-sansio` trên loopback; **một** worker outbox; PostgreSQL 16 và Redis 8 trong Docker Compose local. API dùng pool PostgreSQL mặc định của repo (10 kết nối, tối đa thêm 10). Đo thời gian từ cùng máy phát tải.
- Dữ liệu nằm ở `ma_soi_load`, tách khỏi DB demo `ma_soi`. DB thử đã áp dụng migration đến `0024_chat_revisions`. Có một ca smoke 2 phòng và ca thử 30 giây trong DB này, nhưng mọi số liệu sự kiện của báo cáo chính được lọc theo ID phòng mới của từng lượt chạy.
- Bài này kiểm tra API, WebSocket và DB. Không có render trình duyệt, TLS, reverse proxy hay độ trễ mạng ngoài máy; cần lặp lại trên hạ tầng staging trước khi cam kết cho người chơi thật.

### Chạy lại

Tạo DB riêng và áp dụng migration:

```bash
docker compose up -d postgres redis
docker compose exec -T postgres createdb -U ma_soi ma_soi_load
export DATABASE_URL=postgresql+psycopg://ma_soi:ma_soi_local_only@127.0.0.1:5433/ma_soi_load
.venv/bin/alembic -c backend/alembic.ini upgrade head
```

Chạy API tại cổng 8001 với `APP_ENV=local`, `APP_ORIGINS=http://localhost:5173`, `REDIS_URL=redis://127.0.0.1:6380/0`, một Uvicorn worker và `--ws websockets-sansio --no-access-log`. Chạy `PYTHONPATH=backend .venv/bin/python -m app.outbox_worker` trong terminal khác với cùng `DATABASE_URL` và `REDIS_URL`. Sau đó:

```bash
DATABASE_URL=postgresql+psycopg://ma_soi:ma_soi_local_only@127.0.0.1:5433/ma_soi_load \
  PYTHONPATH=backend .venv/bin/python scripts/load_step_9_5.py \
  --report docs/step-9/load-result-local.json
```

Script từ chối seed nếu tên DB khác `ma_soi_load`. Có thể dùng `--duration 30` để kiểm tra nhanh; `--burst-span 0` dồn 50 lệnh cùng lúc, còn mặc định `1.0` phát 50 lệnh trong một giây. Lượt đo này đã lưu hai file JSON rồi dừng API/worker và xóa riêng DB `ma_soi_load`; DB demo `ma_soi` không bị tác động.

## Kết quả

Hai lượt 10 phút đều dùng 20 phòng, 500 WebSocket, 25 tin/giây và 50 lệnh ở giữa bài. [Lượt chuẩn 50 lệnh/giây](load-result-local.json) phát 50 yêu cầu trải trong một giây; [lượt dồn đồng thời](load-result-local-simultaneous.json) phát cả 50 ngay cùng thời điểm. Có thêm lượt kiểm tra ngắn 30 giây trước lượt chuẩn (750 tin, p95 lệnh quản trò 77,1 ms), không dùng để kết luận ngưỡng 10 phút.

| Chỉ số | 50 lệnh trong 1 giây | 50 lệnh cùng lúc | Mục tiêu |
| --- | ---: | ---: | ---: |
| Chat HTTP thành công / đã phát | 15.000 / 15.000 | 15.000 / 15.000 | Không lỗi |
| Lượt nhận WebSocket / cần nhận | 375.000 / 375.000 | 375.000 / 375.000 | Không thiếu, không trùng |
| Chat đến từng người, p95 / p99 | 287,7 / 343,3 ms | 285,3 / 309,5 ms | 99% ≤ 2.000 ms |
| Tin đến người cuối, p99 | 343,5 ms | 309,9 ms | 99% ≤ 2.000 ms |
| Tỷ lệ lượt nhận ≤ 2 giây | 100% | 100% | ≥ 99% |
| Lệnh quản trò, p95 / p99 | **77,9 / 81,2 ms** | **693,3 / 704,9 ms** | p95 ≤ 500 ms |
| Lệnh quản trò ≤ 500 ms | 100% | 64% | ≥ 95% |
| Phiếu vote, p95 | 77,5 ms | 819,4 ms | Ghi để theo dõi |
| Chat đã xác nhận nhưng thiếu trong DB | 0 | 0 | 0 |
| Outbox / tổng sự kiện của phòng thử | 15.095 / 15.095 | 15.095 / 15.095 | Khớp |

**Kết luận:** kịch bản đúng chỉ tiêu **50 lệnh trong một giây** đạt cả hai ngưỡng độ trễ và không mất sự kiện đã commit. Với **50 lệnh phát đồng thời**, độ trễ quản trò không đạt mục tiêu; đây là giới hạn hiện tại của cấu hình một API/worker local, không được gộp vào kết quả đạt. Khi thử tải staging ở 10.4, đo lại cả hai dạng burst; nếu muốn cam kết 50 thao tác cùng một thời điểm, cần cải thiện thêm đường ghi/khả năng xử lý song song và đo lại. Lượt 10 phút không thay thế kiểm tra UI trình duyệt hoặc đường mạng thật.

Đối chiếu ở lượt chuẩn: PostgreSQL có 15.000 `chat.sent`, 25 `vote.ballot_submitted`, 25 `event.announcement_decided`, 25 `event.custom_announcement` và 20 `vote.opened`; tổng 15.095 sự kiện khớp 15.095 hàng outbox. Không có HTTP lỗi, frame trùng hay WebSocket đóng bất thường. Bản xuất JSON giữ p50/p95/p99/max cho HTTP chat, lệnh quản trò, phiếu vote, từng lượt nhận và tin đến người cuối. Tại một mốc giữa lượt đầu, API dùng khoảng 123 MiB RSS, PostgreSQL khoảng 100 MiB và Redis khoảng 13 MiB; đây là mẫu tức thời, không phải đỉnh bộ nhớ.
