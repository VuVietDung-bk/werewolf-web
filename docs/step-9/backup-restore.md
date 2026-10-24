# Bước 9.2 · Sao lưu, khôi phục và lưu trữ

## Công cụ và giới hạn

`scripts/postgres_maintenance.sh` dùng `pg_dump -Fc` để sao lưu toàn bộ DB và ghi SHA-256 cạnh file. Chạy `PG_CLIENT=compose` mặc định với PostgreSQL 16 trong Docker Compose local; khi triển khai có PostgreSQL client phù hợp trên host, dùng `PG_CLIENT=host` và các biến chuẩn `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`. File sao lưu chứa thông tin tài khoản, chat và nhật ký kín: thư mục/file được tạo với quyền riêng (`umask 077`), không đưa vào git, không chia sẻ qua kênh công khai. Bản sao ở cùng máy chỉ phục vụ kiểm tra local; vận hành thật cần chuyển bản sao sang kho riêng có mã hóa, kiểm soát quyền và giám sát dung lượng.

```bash
BACKUP_DIR=backups bash scripts/postgres_maintenance.sh backup
bash scripts/postgres_maintenance.sh restore-check backups/ma_soi-YYYYMMDDTHHMMSSZ.dump restore_20261022
```

`restore-check` kiểm tra SHA-256, đọc danh mục archive, từ chối tên DB ngoài `restore_*` hoặc DB đã tồn tại, tạo DB mới và dùng `pg_restore --exit-on-error`. Lệnh in migration cuối và số `rooms`, `room_members`, `game_events`, `outbox`. Nếu khôi phục lỗi, DB thử được giữ lại để điều tra. Sau khi đối chiếu, người vận hành tự xóa **đúng DB thử**, ví dụ `docker compose exec -T postgres dropdb -U ma_soi restore_20261022`. Không chạy khôi phục vào DB chính.

## Lịch vận hành đã chốt cho dữ liệu phòng

- **Hàng ngày 01:00 UTC:** chạy `backup-prune 7` độc lập để dọn bản sao local trước khi chúng vượt quá 7 ngày. Vì job chỉ chạy một lần mỗi ngày, nó chọn các bản đã hơn 6 ngày tuổi; bản tạo lúc 02:00 sẽ được dọn gần cuối ngày thứ 7. Kho bản sao ngoài máy cần mã hóa, kiểm soát quyền và lifecycle tự hết hạn sau **7 ngày từ lúc tạo**; cấu hình kho sẽ được cài ở bước 10.4.
- **Hàng ngày 02:00 UTC:** chạy `daily`; lệnh sao lưu trước và chỉ xóa phòng đủ hạn nếu sao lưu thành công. Kiểm tra mã thoát, file SHA-256 và dung lượng. Dọn bản sao chạy riêng nên backup lỗi không kéo dài thời gian giữ file cũ khi scheduler còn hoạt động.
- **Mỗi tuần:** chọn bản sao gần nhất, chạy `restore-check` vào DB mới, đối chiếu số phòng/thành viên/sự kiện với một mốc ổn định hoặc bản ghi snapshot, ghi thời gian khôi phục, rồi xóa DB thử. Thử thêm một bản sao cũ định kỳ để phát hiện bản hỏng.
- **Cùng lượt sau backup:** `daily` chạy `retention-delete 90`. Chủ dự án đã chọn **90 ngày từ thời điểm kết thúc ván**. Script từ chối số ngày khác và cần `RETENTION_POLICY_APPROVED=1`; dùng `retention-preview 90` để xem số phòng/sự kiện sắp bị xóa. Mỗi lượt xóa tối đa 100 phòng đã `finished` và có `ended_at` cũ hơn ngưỡng; chạy nhiều lượt nếu còn dữ liệu. Xóa phòng sẽ xóa theo khóa ngoại các thành viên, chat, role, vote, sự kiện và outbox liên quan, nhưng giữ tài khoản và phiên dùng ở phòng khác. Người chơi được thông báo và có thể xuất transcript trước mốc xóa.
- `backup-prune 7` chỉ dọn file `.dump` do script tạo cho đúng DB và file `.sha256` đi kèm; `backup-prune-preview 7` cho xem số bản sắp dọn. Lệnh dọn từ chối thời hạn khác 7 ngày và từ chối xóa nếu thiếu cờ `RETENTION_POLICY_APPROVED=1`.

Mẫu lịch ở [`deploy/maintenance.cron.example`](../../deploy/maintenance.cron.example) dọn backup local lúc 01:00 UTC, chạy backup rồi xóa phòng đủ hạn lúc 02:00 UTC. Đây là cấu hình để cài trên host triển khai ở bước 10.4; chưa có scheduler production để cài ngay. Trước khi bật cron, cấu hình kho backup riêng, công cụ PostgreSQL client tương thích, giám sát mã thoát, thông tin xác thực libpq và kiểm tra một bản sao đã khôi phục thành công. Lịch cron chạy theo múi giờ của host, vì vậy host cần đặt UTC. Không dùng bản sao trên cùng máy làm bản duy nhất.

Lịch chạy mỗi ngày nên việc xóa có thể diễn ra sau mốc 90 ngày tối đa một chu kỳ; phòng còn trong lô quá 100 sẽ đợi lượt sau. Trước khi bật lịch, xác nhận chức năng xuất transcript hoạt động. Bản sao toàn DB tạo ngay trước lần xóa có thể còn chứa dữ liệu phòng thêm tối đa 7 ngày: tổng thời gian kể từ cuối trận có thể gần 98 ngày. Nếu scheduler ngừng, cần xử lý hàng đợi và file quá hạn ngay khi phục hồi. Bước 10.3 sẽ hoàn thiện hướng dẫn công khai; bước 10.4 phải cấu hình lifecycle 7 ngày cho cả kho ngoài máy và cảnh báo khi job thất bại.

## Biên bản diễn tập local · 2026-10-21 UTC

- Nguồn: DB riêng `restore_source_9_2`, PostgreSQL 16.15, migration `0024_chat_revisions`, dữ liệu giả gồm 3 phòng (một cũ 100 ngày, một đang chơi, một mới kết thúc 20 ngày), 1 thành viên, 1 sự kiện, 1 outbox, 2 tài khoản.
- `backup`: archive custom 56.657 byte, SHA-256 tạo thành công, thời gian script đo **< 1 giây**.
- `restore-check`: kiểm tra hash đạt; khôi phục vào DB mới `restore_step9_2_check` trong **1 giây** (độ phân giải bộ đếm là 1 giây). Migration và số hàng khớp nguồn: phòng 3, thành viên 1, sự kiện 1, outbox 1. Lệnh từ chối khôi phục lần hai vào tên DB này.
- Kiểm tra bổ sung phát hiện checksum ban đầu có thể tham chiếu nhầm file cùng thư mục khi đổi tên archive. Script đã được sửa để so khớp **tên archive được yêu cầu và hash của chính file đó**; chạy lại restore thành công. File đổi tên kèm sidecar cũ và file bị sửa sau khi tạo checksum đều bị từ chối trước khi tạo DB.
- `retention-preview 90`: 1 phòng, 1 sự kiện đủ điều kiện. `retention-delete 90` thiếu cờ chốt chính sách trả lỗi; khi bật cờ **chỉ trên DB thử đã khôi phục**, xóa đúng 1 phòng cũ. Phòng đang chơi và phòng mới kết thúc còn lại; thành viên, sự kiện và outbox của phòng cũ bị xóa qua khóa ngoại; 2 tài khoản vẫn còn.
- Kiểm tra dọn backup trên file giả quanh ngưỡng an toàn: bản 145 giờ tuổi và checksum bị xóa, bản 143 giờ tuổi cùng file của DB khác vẫn còn; thiếu cờ cho phép thì không xóa. `daily` gặp lỗi backup sẽ dừng trước lệnh xóa phòng.
- Đây là diễn tập chức năng trên dữ liệu nhỏ, chưa đo RTO/RPO trên dữ liệu thật hoặc kiểm chứng kho sao lưu ngoài máy. Việc đó thuộc giai đoạn triển khai/pilot.
