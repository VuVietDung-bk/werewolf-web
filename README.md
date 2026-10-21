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

### Tự thử với quản trò và người chơi ảo

Chế độ này chỉ hoạt động khi API chạy local và được bật rõ ràng. Trong terminal API, đặt thêm `ENABLE_DEMO_MODE=1` rồi khởi động lại Uvicorn. Sau khi API và Vite đã chạy, mở terminal khác tại thư mục gốc:

```bash
.venv/bin/python scripts/create_demo.py
```

Lệnh tạo một phòng chờ với 1 quản trò, 5 người chơi thử và 5 loại role mẫu, đủ để mỗi người chọn một role riêng. Mở đường dẫn lệnh in ra, chọn tài khoản ở thanh **Trải nghiệm local**, rồi bấm **Chuyển tài khoản**. Quản trò có thể sửa role, đóng đăng ký, phát role và bắt đầu trận. Chuyển sang từng người chơi để xem role riêng, tab tin riêng và thông báo. Các tài khoản thử không tự chat hay ra quyết định. Mỗi tab cùng trình duyệt dùng chung cookie; muốn xem đồng thời nhiều vai, dùng các hồ sơ trình duyệt riêng.

Chạy lại lệnh sẽ in lại đường dẫn phòng cũ. Thêm `--new` để tạo phòng mẫu mới mà vẫn giữ phòng cũ trong DB. Mã chuyển vai nằm trong `.local-demo.json` đã được gitignore; chỉ dùng đường dẫn này trên máy local và không chia sẻ. Khi không cần nữa, khởi động API mà không có `ENABLE_DEMO_MODE=1` để khóa bộ chuyển vai. Nếu dùng cổng khác, truyền `--api-port` và `--web-port` cho script, đồng thời đặt `API_PROXY_TARGET` cho Vite và thêm origin của Vite vào `APP_ORIGINS`.

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

Quản trò đặt tên bộ role, nạp bộ mẫu hoặc tự tạo/sửa/xóa role ở sảnh. Mô tả role chứa toàn bộ luật chữ; app không tự xử lý kỹ năng. Mỗi role có trường “Tiên tri soi ra” độc lập với phe thật; màu thẻ giúp phân biệt Sói, Dân/Làng và Trung lập. Thành viên xem danh mục đầy đủ, nhưng chỉ thấy role được phát cho mình. Sau khi đóng đăng ký, quản trò chọn cách phát ở cùng một khu vực: ngẫu nhiên theo số bản role, hoặc cho từng người chọn tối đa một trong ba role theo thứ tự xáo. Ở cách thứ hai, quản trò chọn ít nhất một loại role ứng viên cho mỗi người, không đặt số ghế phe; role được chọn rời kho và không thể được chọn lại. Lượt cuối có thể thấy một hoặc hai role. Người chơi có thể bấm Random từ toàn bộ kho còn lại, quản trò có thể chọn hộ kèm lý do. Kết quả, lượt chọn và snapshot được lưu trong PostgreSQL; sau khi phát, bộ role của ván bị khóa. Quản trò bắt đầu Ngày 0 khi mọi người đã có role. Chi tiết kiểm thử ở [docs/step-4/progress.md](docs/step-4/progress.md).

Mỗi role có thể dùng icon pixel mặc định, chọn từ 7 icon mẫu hoặc tải PNG/WebP/JPEG tĩnh từ máy của quản trò trước khi phát role. Giới hạn tải lên là 1 MiB và 512 × 512 px. Server chuẩn hóa ảnh thành PNG, lưu trong PostgreSQL và chỉ phục vụ ảnh upload cho thành viên phòng. Icon mẫu công khai trong danh mục; icon bên cạnh tên người chơi chỉ hiện ở phần role riêng hoặc bảng quản trò.

Trong trận, quản trò chuyển pha thủ công theo Ngày 0 → Đêm 1 → Ngày 1, tạm dừng kèm lý do, tiếp tục hoặc kết thúc bằng kết quả và giải thích. Các nút nằm ở tab Trận đấu; người chơi thấy trạng thái tạm dừng và kết quả đã công bố. Không có luật role hay điều kiện thắng tự chạy. Migration `0008_game_flow` cần được áp dụng trước khi chạy phiên bản này. Chi tiết ở [docs/step-5/progress.md](docs/step-5/progress.md).

Các lệnh điều khiển trận và thao tác quản trò do giao diện gửi kèm `client_request_id`. Nếu lần gửi bị gián đoạn, thử lại cùng nội dung trong phiên trang sẽ dùng lại mã này; API không tạo thêm thao tác đã ghi. Lệnh mới khi trận đã kết thúc hoặc trạng thái không phù hợp vẫn bị từ chối.

Tab **Bàn quản trò** lưu trạng thái kín từng người, cho phép giết/hồi sinh, thêm/gỡ hiệu ứng, ghi chú, đổi role/phe/đội chat và gửi tin riêng. Hộp thoại người chơi chia các thao tác thành ba tab. Role gốc được giữ trong bản phân; role hiện tại chỉ người sở hữu và quản trò thấy. Người chơi nhận tin riêng trong tab **Tin riêng**; quản trò chỉ cần nhập nội dung, không cần lý do gửi. Quản trò công bố chết/hồi sinh riêng sau khi quyết định; nguyên nhân và role công bố là tùy chọn. Tab **Thông báo** chỉ hiện trạng thái chưa công bố cho quản trò và có đường sang Bàn quản trò để công bố; người chơi chỉ thấy thông tin đã công bố. Danh sách ghế chỉ hiện trạng thái sống/chết đã công bố. Quản trò dùng nút **Sửa/Xóa** ngay cạnh ghi chú, tin riêng đã gửi hoặc thông báo đã đăng; nội dung xóa không còn trong danh sách, nhưng bản gốc và thao tác sửa/xóa vẫn được lưu thành sự kiện để công bố sau trận. Sửa thông báo chết/hồi sinh hiện chỉ áp dụng cho nguyên nhân công khai; xóa thẻ không đảo trạng thái sống/chết hay thu hồi role đã công bố. Chạy `alembic -c backend/alembic.ini upgrade head` để áp dụng các migration mới nhất trước khi dùng phiên bản này.

Bảng quản trò có bộ lọc sống/chết, phe, hiệu ứng và người chờ công bố; dữ liệu tải lại sau thao tác và định kỳ khi mở. Màn trận và bảng quản trò dùng nền làng theo ngày/đêm, còn trạng thái hiệu ứng, chết, hồi sinh có biểu tượng pixel kèm nhãn chữ.

Thông báo công khai tùy chỉnh có loại sự kiện, người liên quan và nguồn gây ra tùy chọn. Quản trò thấy bản xem trước trước khi đăng; chọn nguồn gây ra sẽ công khai tên người đó. Có thể gắn thông báo với thao tác hoặc quyết định vote đã làm để đối chiếu sau trận; liên kết này và lý do kín không xuất hiện trong API thông báo của người chơi. Chọn loại “Cái chết” hay “Hồi sinh” chỉ đổi nhãn thẻ, không đổi trạng thái người chơi. [Chi tiết bước 7](docs/step-7/progress.md).

Khi có người chết hoặc hồi sinh kín, quản trò có thể **Giữ kín**, **Bỏ qua thông báo** hoặc công bố sau khi xác nhận. Giữ kín/bỏ qua cần lý do và được lưu trong sự kiện kín; danh sách ghế, tab Thông báo, API phòng và tín hiệu WebSocket công khai vẫn giữ trạng thái cũ. Người bị tác động có thể nhận tín hiệu riêng của thao tác, nhưng người khác không thấy quyết định giữ kín/bỏ qua. Chat chung/đội mặc định chặn gửi khi trạng thái kín hoặc công khai còn là chết, để hồi sinh kín không lộ qua tin nhắn; cấu hình cho người chết gửi chat vẫn được áp dụng. Migration `0023_publication_disposition` cập nhật trạng thái xử lý cho các phòng đang có người chờ công bố. [Chính sách chi tiết và kiểm thử](docs/step-7/progress.md).

Trong tab **Vote**, quản trò có thể mở/đóng vote ban ngày, kể cả Ngày 0. Người chơi được công bố là sống có thể chọn một người được công bố là sống, bỏ qua, đổi/rút phiếu trước khi đóng, hoặc không gửi. Trong trận họ chỉ thấy phiếu của mình; quản trò thấy từng phiếu và bảng tổng hợp. Vòng vote đang mở phải được đóng trước khi chuyển pha hoặc kết thúc trận; màn điều khiển sẽ cảnh báo và dẫn sang tab Vote. Sau khi đóng, quản trò ghi quyết định kèm lý do, kể cả khi thiếu phiếu, hòa hoặc khác số đông. Quản trò có thể công bố riêng kết quả, tên người đã gửi phiếu và lý do trong tab **Thông báo**; chỉ phần được chọn mới hiện công khai. Quyết định và phiếu gốc vẫn kín trong trận; quản trò vẫn tự thao tác thay đổi trạng thái người chơi. [Quy tắc chi tiết](docs/step-5/vote-rules.md).

Tab **Chat** có chat chung, chat đội và chat với quản trò. Chat chung mở từ sảnh, lúc chọn role và Ngày 0, rồi các ngày tiếp theo; mặc định ban đêm chỉ đọc. Chat đội mặc định gửi ban đêm cho đội hiện tại; tin đã nhận vẫn đọc được sau khi đổi đội hoặc chết, còn người mới vào đội không thấy tin cũ. Quản trò có thể đổi quyền chat ban đêm/ban ngày và quyền người chết, kèm lý do và xác nhận; tạm dừng luôn chặn gửi. Chat quản trò kiểm tra quyền mở/trả lời theo role hiện tại; role bị động chỉ trả lời khi quản trò đã mở và cấu hình cho phép. Quản trò chọn đội hoặc người chơi trong tab Chat. Tab **Tin riêng** cũ vẫn hiển thị các thông điệp một chiều quản trò gửi từ Bàn quản trò; người chơi có thể trả lời qua Chat quản trò nếu role cho phép. Chat lưu trong PostgreSQL và tải tin cũ theo trang. WebSocket báo tin mới ngay cho đúng người nhận, tự nối lại với cursor khi đứt mạng; màn Chat cũng hỏi API mỗi 5 giây, tải các trang còn thiếu và bỏ tin trùng theo ID. Tab pixel có dấu **Mới** cho kênh có tin chưa xem. Mỗi tin tối đa 2.000 ký tự; mặc định mỗi người gửi tối đa 6 tin trong 10 giây và 30 tin trong 60 giây trên cả ba kênh. Quản trò có thể chặn/mở quyền gửi chat của từng người kèm lý do; người bị chặn vẫn đọc các kênh được phép. Chạy migration mới nhất trước khi dùng chức năng này. [Chi tiết, ảnh chụp và kiểm thử chat](docs/step-6/progress.md).

Trong trận, quản trò có nút **Sửa/Xóa** ngay trên từng tin do mình gửi ở cả ba kênh. Tin đã sửa cập nhật qua WebSocket hoặc polling; tin đã xóa biến mất khỏi chat hiện tại. Sau trận, người từng tham gia xem được mọi kênh, kể cả tin đã xóa, và mở lịch sử từng tin để đối chiếu bản gốc với các bản sửa/xóa. Tab **Thông báo** có danh sách lịch sử sửa/xóa thông báo sau trận, gồm cả thông báo đã bị xóa. Mọi thao tác lưu sự kiện chỉ thêm mới; bản tổng kết toàn ván có lọc và xuất dữ liệu nằm ở bước 8.

Yêu cầu role và thông báo dùng icon pixel SVG theo trạng thái hoặc loại công khai, luôn kèm nhãn chữ. Quản trò có icon ổ khóa trung tính ở danh sách trạng thái đang giữ kín; người chơi không thấy phần này trước khi công bố. [Ảnh hai quyền xem và chi tiết bước 7](docs/step-7/progress.md).

Tab **Yêu cầu role** cho người chơi gửi nội dung tự do đến quản trò, không bắt nhập loại kỹ năng hay số mục tiêu. Server xét role hiện tại, pha được phép, sống/chết và trạng thái tạm dừng trước khi nhận. Người chơi chỉ xem yêu cầu của mình; quản trò xem danh sách đã nhận. Quản trò đánh dấu đang xét, chấp nhận hoặc từ chối kèm phản hồi; người gửi có thể rút trước khi chốt. Khi chấp nhận, quản trò có thể liên kết thao tác đã thực hiện ở Bàn quản trò. Gửi yêu cầu hoặc thay đổi trạng thái yêu cầu không tự tạo hiệu ứng. Mỗi thay đổi được lưu thành sự kiện kín; tab tự cập nhật cả phản hồi trên yêu cầu cũ. Tối đa 2.000 ký tự, 3 yêu cầu/30 giây và 10 yêu cầu/5 phút cho mỗi người trong phòng. Cần chạy migration mới nhất để cập nhật bảng `role_requests`. [Chi tiết và kiểm thử](docs/step-7/progress.md).

Các thao tác ảnh hưởng đến trận và thông báo công khai mở hộp xác nhận có pha, đối tượng, phạm vi và bản xem trước trước khi gửi lệnh. Nút **Hủy** và phím Escape đóng hộp mà không ghi thao tác. [Kiểm tra hành vi](docs/step-5/confirmation-verification.md).

Mở `http://localhost:5173/?asset-demo=1` để thử ảnh và phương án dự phòng mà không tải ảnh thử trên trang thường. Quy ước file nằm trong [frontend/public/assets/README.md](frontend/public/assets/README.md); ảnh Tiên Tri hiện chỉ là concept.

## Staging và CI

`.env.example` và `.env.staging.example` là mẫu biến môi trường; không chứa thông tin thật. CI build frontend, chạy migration rồi kiểm tra API/DB và đường xuống trên PostgreSQL sạch. Hạ tầng staging sẽ được hoàn thiện trước pilot.
