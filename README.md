# Ma Sói Web

Nền tảng mới cho trò chơi Ma Sói do quản trò điều hành. Mã bot Discord cũ nằm trong `archives/` để tham khảo. [Kế hoạch](WEB_PLAN.md) và [checklist](CHECKLIST.md) theo dõi các bước triển khai.

## Chạy local

Yêu cầu: Python 3.10+, Node 22.12+, npm, Docker Compose (cho PostgreSQL ở các bước dùng DB). Compose dùng cổng host **5433** để tránh trùng PostgreSQL có sẵn ở 5432.

```bash
docker compose up -d postgres
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e './backend[dev]'
export DATABASE_URL=postgresql+psycopg://ma_soi:ma_soi_local_only@127.0.0.1:5433/ma_soi
export APP_ENV=local
alembic -c backend/alembic.ini upgrade head
uvicorn app.main:app --app-dir backend --reload --port 8000
```

Trong terminal khác:

```bash
cd frontend
npm ci
npm run dev
```

Mở `http://localhost:5173`. API liveness ở `http://localhost:8000/api/health/live`. Vite chuyển tiếp đường dẫn `/api` tới API local.

Trang vào làng dùng để đăng nhập, tạo phòng, vào phòng và mở lại phòng đã tham gia. Sau khi mở phòng, thanh điều hướng chuyển giữa sảnh chờ, danh mục role, phần quản trò thiết lập/phát role và màn phân role riêng. Khi bắt đầu Ngày 0, tab sảnh chờ biến mất và tab trận đấu xuất hiện. Màn hiện tại được lưu trong URL; bản nháp chọn chế độ phát và role ứng viên được giữ trong phiên trình duyệt khi đổi tab hoặc tải lại.

Nếu Uvicorn đã chạy trước khi bạn `export DATABASE_URL` và `APP_ENV`, hãy dừng rồi chạy lại tiến trình đó; biến môi trường mới không tự được nạp vào tiến trình đang chạy. Khi dùng cổng khác, đặt `API_PROXY_TARGET` cho Vite và thêm Origin của frontend vào `APP_ORIGINS` của API.

Kiểm tra migration (terminal đã kích hoạt `.venv`):

```bash
export DATABASE_URL=postgresql+psycopg://ma_soi:ma_soi_local_only@127.0.0.1:5433/ma_soi
alembic -c backend/alembic.ini check
```

`alembic -c backend/alembic.ini downgrade base` chỉ dùng với DB thử nghiệm trống để kiểm tra đường xuống; lệnh này gỡ tất cả bảng của migration nền tảng.

## Phiên khách và bảo vệ API

Mở giao diện ở `http://localhost:5173`, nhập tên để tạo phiên khách. Cookie phiên có `HttpOnly`, `SameSite=Lax`, thời hạn 30 ngày; token ngẫu nhiên chỉ được lưu dạng SHA-256 trong DB. Tải lại trang sẽ gọi `/api/auth/me` rồi lấy các phòng và ghế hiện có. Đăng xuất thu hồi phiên trong DB.

Các lệnh HTTP thay đổi dữ liệu và kết nối WebSocket phải có `Origin` nằm trong `APP_ORIGINS`. Cookie mặc định có `Secure`; chỉ đặt `APP_ENV=local` khi dùng HTTP trên máy phát triển. Mọi quyền phòng được lấy từ DB; role/phe do client khai không cấp quyền.

## Sảnh chờ

Quản trò tạo phòng từ giao diện, chọn 2–30 ghế và chia sẻ mã mời 12 ký tự. Người chơi nhập mã và xác nhận rằng toàn bộ chat đội, chat riêng với quản trò, yêu cầu role và quyết định quản trò sẽ được công bố cho người từng tham gia phòng sau trận. Quản trò không chiếm ghế. Trong sảnh, quản trò có thể đổi tên, số ghế và mở/đóng đăng ký; người chơi có thể rời rồi vào lại khi đăng ký còn mở. Danh sách ghế lưu trong PostgreSQL, khôi phục sau khi tải lại hoặc khởi động lại API.

API: `POST /api/rooms`, `POST /api/rooms/join`, `GET /api/rooms/mine`, `GET /api/rooms/{id}`, `PATCH /api/rooms/{id}/settings`, `POST /api/rooms/{id}/leave`. Các thao tác thay đổi phòng ghi sự kiện và outbox trong cùng giao dịch. Cấp ghế khóa hàng phòng để tránh vượt giới hạn khi có nhiều người vào cùng lúc.

Sự kiện phòng dùng `game_events` theo số thứ tự và `outbox` ghi trong cùng giao dịch. Hàm `app.db.events.append_event` nhận session đang trong giao dịch; caller phải commit sau khi cập nhật trạng thái. Worker phát outbox sẽ được thêm ở bước vận hành.

## Role tùy chỉnh và cách phát role

Quản trò đặt tên bộ role, nạp bộ mẫu hoặc tự tạo/sửa/xóa role ở sảnh. Mô tả role chứa toàn bộ luật chữ; app không tự xử lý kỹ năng. Mỗi role có trường “Tiên tri soi ra” độc lập với phe thật; màu thẻ giúp phân biệt Sói, Dân/Làng và Trung lập. Thành viên xem danh mục đầy đủ, nhưng chỉ thấy role được phát cho mình. Sau khi đóng đăng ký, quản trò chọn cách phát ở cùng một khu vực: ngẫu nhiên theo số bản role, hoặc cho từng người chọn tối đa một trong ba role theo thứ tự xáo. Ở cách thứ hai, quản trò chọn kho ứng viên và quyền trùng role, không đặt số ghế phe; người chơi có thể bấm Random từ toàn bộ kho còn hợp lệ, quản trò có thể chọn hộ kèm lý do. Kết quả, lượt chọn và snapshot được lưu trong PostgreSQL; sau khi phát, bộ role của ván bị khóa. Quản trò bắt đầu Ngày 0 khi mọi người đã có role. Chi tiết kiểm thử ở [docs/step-4/progress.md](docs/step-4/progress.md).

Mỗi role có thể dùng icon pixel mặc định, chọn từ 7 icon mẫu hoặc tải PNG/WebP/JPEG tĩnh từ máy của quản trò trước khi phát role. Giới hạn tải lên là 1 MiB và 512 × 512 px. Server chuẩn hóa ảnh thành PNG, lưu trong PostgreSQL và chỉ phục vụ ảnh upload cho thành viên phòng. Icon mẫu công khai trong danh mục; icon bên cạnh tên người chơi chỉ hiện ở phần role riêng hoặc bảng quản trò.

Trong trận, quản trò chuyển pha thủ công theo Ngày 0 → Đêm 1 → Ngày 1, tạm dừng kèm lý do, tiếp tục hoặc kết thúc bằng kết quả và giải thích. Các nút nằm ở tab Trận đấu; người chơi thấy trạng thái tạm dừng và kết quả đã công bố. Không có luật role hay điều kiện thắng tự chạy. Migration `0008_game_flow` cần được áp dụng trước khi chạy phiên bản này. Chi tiết ở [docs/step-5/progress.md](docs/step-5/progress.md).

Tab **Bàn quản trò** lưu trạng thái kín từng người, cho phép giết/hồi sinh, thêm/gỡ hiệu ứng và ghi chú. Người chơi không nhận trạng thái kín qua API phòng. Công bố chết/hồi sinh và đổi role/phe sẽ được bổ sung ở phần tiếp theo của bước 5. Chạy `alembic upgrade head` để có migration `0009_player_state`.

Mở `http://localhost:5173/?asset-demo=1` để thử ảnh và phương án dự phòng mà không tải ảnh thử trên trang thường. Quy ước file nằm trong [frontend/public/assets/README.md](frontend/public/assets/README.md); ảnh Tiên Tri hiện chỉ là concept.

## Staging và CI

`.env.example` và `.env.staging.example` là mẫu biến môi trường; không chứa thông tin thật. CI build frontend, chạy migration rồi kiểm tra API/DB và đường xuống trên PostgreSQL sạch. Hạ tầng staging sẽ được hoàn thiện trước pilot.
