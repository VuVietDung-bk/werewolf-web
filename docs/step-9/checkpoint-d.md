# Kiểm tra D · sau bước 8–9

Ngày chạy: 25/10/2026. Dữ liệu giả, DB riêng `ma_soi_checkpoint_d` cho D.1–D.3/D.5 và `ma_soi_load` cho D.4. DB demo `ma_soi` không dùng trong diễn tập. PostgreSQL 16 và Redis 8 chạy bằng Docker Compose local.

## D.1 · Một ván tích hợp và quyền trước/sau trận

`backend/tests/test_transcript.py` chạy một ván từ sảnh qua Ngày 0, Đêm 1 và kết thúc. Cùng ván có chat chung, chat đội, chat quản trò; yêu cầu role và phản hồi; vote chỉ một trong hai người gửi phiếu, quản trò quyết định không loại ai và chọn phần công bố; đổi phe, giết kín, giữ rồi bỏ qua thông báo chết, hồi sinh, thông báo tấn công công khai, sửa/xóa nội dung và đính chính sau trận. Trước khi kết thúc, API transcript trả 409; khi người chết đang bị giữ kín, ghế công khai vẫn sống và danh sách người chết trống. Sau trận, người từng rời phòng đọc được toàn bộ transcript; người ngoài nhận 403. Số thứ tự sự kiện không trùng/lùi; JSON và CSV xuất lại giống nhau; bản gốc không đổi sau đính chính. **Kết quả: 1 passed.**

## D.2 · Khởi động lại API và mất Redis/worker

[`scripts/checkpoint_d_runtime.py`](../../scripts/checkpoint_d_runtime.py) tạo một phòng hai người chơi trên DB thử và giữ WebSocket mở. Sau tin đầu, script dừng worker và Redis rồi gửi tin thứ hai: WebSocket vẫn nhận sự kiện từ PostgreSQL qua polling. Sau khi khởi động lại API trong lúc Redis còn tắt, trạng thái phòng và hai ghế giữ nguyên; kết nối từ cursor cũ nhận lại sự kiện số 9 và 10 theo thứ tự. Bật lại Redis/worker: hàng outbox chưa phát của phòng từ 1 về 0. Số sự kiện của phòng tăng từ 7 lên 10, không mất sự kiện đã commit. [Số liệu máy đọc được](checkpoint-d-runtime.json). **Kết quả: đạt.**

## D.3 · Khôi phục và đối chiếu dữ liệu

[`scripts/checkpoint_d_snapshot.py`](../../scripts/checkpoint_d_snapshot.py) ghi số hàng và SHA-256 của từng hàng sau khi sắp thứ tự ổn định cho **toàn bộ 22 bảng**. [Snapshot nguồn](checkpoint-d-before-restore.json) được ghi trước khi `pg_dump -Fc` tạo bản sao 60.900 byte và file checksum. `restore-check` xác minh SHA-256, khôi phục vào DB mới `restore_checkpoint_d_20261025` trong khoảng **1 giây**. [Snapshot khôi phục](checkpoint-d-after-restore.json) giống hệt nguồn (`cmp` đạt): **2 phòng, 3 thành viên, 14 sự kiện, 14 outbox** và các bảng còn lại đều khớp cả số hàng lẫn hash nội dung. Không ghi đè DB nguồn. **Kết quả: đạt.**

## D.4 · Ma trận quyền và tải 500 người

Toàn bộ `backend/tests` chạy lại sau sửa mã: **36 passed**, gồm quyền HTTP/WebSocket, phiên, chat đội/riêng, yêu cầu, vote, thông báo kín, transcript và người ngoài phòng. Bài tải dùng [`scripts/load_step_9_5.py`](../../scripts/load_step_9_5.py), 20 phòng × 25 người, 500 WebSocket, 25 tin/giây trong 600 giây và 50 lệnh trải trong một giây ở giữa bài. Mỗi bài seed phòng mới trên DB `ma_soi_load`, chỉ đếm sự kiện của các phòng vừa tạo.

Lượt đầu [không đạt](checkpoint-d-load-result.json): p95 lệnh quản trò **800,9 ms** dù toàn bộ chat/WebSocket/DB đều đủ. Ca [chẩn đoán 30 giây](checkpoint-d-load-diagnostic.json) cũng dao động đến **332,6 ms**. API lúc đó ghi mỗi HTTP thành công ở INFO vào stdout, hơn 15.000 dòng trong bài dài. Đã giữ metrics cho mọi request nhưng chuyển log 2xx/3xx xuống DEBUG, 4xx lên WARNING, 5xx lên ERROR; mẫu route vẫn che query/cookie. Ca [30 giây sau sửa](checkpoint-d-load-after-log-fix.json) có p95 quản trò **101,6 ms**. Lượt [xác nhận 10 phút sau sửa](checkpoint-d-load-final.json) đạt các ngưỡng:

| Chỉ số | Kết quả cuối | Mục tiêu |
| --- | ---: | ---: |
| Tin chat HTTP thành công / gửi | 15.000 / 15.000 | Không lỗi |
| Lượt nhận WebSocket / cần nhận | 375.000 / 375.000 | Không thiếu/trùng |
| Lượt nhận chat p99 | 322,1 ms | ≤ 2.000 ms |
| Lượt nhận chat ≤ 2 giây | 100% | ≥ 99% |
| Lệnh quản trò p95 | **384,8 ms** | ≤ 500 ms |
| Lệnh quản trò ≤ 500 ms | 100% | ≥ 95% |
| Tin đã xác nhận thiếu trong PostgreSQL | 0 | 0 |
| Sự kiện thiếu outbox | 0/15.095 | 0 |

Lượt dài cuối còn ghi p95 phiếu vote 474,8 ms, không có lỗi HTTP hoặc WebSocket. Mục tiêu của checklist là **50 lệnh trong một giây**; ca 50 lệnh cùng đúng một thời điểm ở bước 9.5 từng vượt ngưỡng và chưa được xác nhận lại sau sửa. Cần đo trên staging ở 10.4 nếu muốn cam kết mức dồn đồng thời đó. Bài local không đo TLS, reverse proxy và mạng của người chơi thật. **Kết quả D.4: đạt theo ngưỡng đã đặt.**

## D.5 · Ván thử trên giao diện thật

[`scripts/checkpoint_d_visual.py`](../../scripts/checkpoint_d_visual.py) tạo phòng hai người và hai role khác phe qua API thật, phát role, đi đến Ngày 1, gửi chat và đăng thông báo. Chromium mở bản frontend **production build** qua Vite preview, chụp [bàn quản trò desktop](screenshots/d5-host-desktop.png), [màn trận người chơi 390 px](screenshots/d5-player-mobile.png), [danh mục role](screenshots/d5-catalog-mobile.png) và [tab Thông báo](screenshots/d5-notice-mobile.png).

Khi chặn toàn bộ `/assets/v1/*`, [màn trận](screenshots/d5-missing-art-mobile.png) vẫn đọc được trên nền màu. Bài này phát hiện icon role và icon dự phòng đều có thể lỗi khiến trình duyệt vẽ ảnh hỏng; `RoleIcon` nay dùng ký hiệu CSS trong trường hợp đó. [Danh mục sau sửa](screenshots/d5-missing-art-catalog-mobile.png) vẫn có tên, phe và mô tả từng role. Mọi màn đã thử có `img` với `alt`, không tràn ngang và không có lỗi JavaScript. Trang người chơi không có tên role gắn với ghế chưa công bố; URL asset tĩnh không chứa ID phòng/người giữ role. [Kết quả trình duyệt](checkpoint-d-visual.json) lưu danh sách request.

Trên kết nối giả lập độ trễ 150 ms, tải xuống 200.000 byte/giây và cache tắt, tiêu đề màn trận xuất hiện sau **2,07 giây**, ảnh nền ngày hoàn tất sau **3,96 giây**. Số liệu này đo trên localhost với API thật và bản build, chưa đại diện cho staging/thiết bị yếu. **Kết quả D.5: đạt.**

## Cổng kết luận

**D.1–D.5 đạt trên môi trường local.** Các phép thử staging về TLS/proxy, vòng đời backup ngoài máy và tình huống 50 lệnh đến cùng một thời điểm còn thuộc bước 10.4.
