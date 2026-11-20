# UX3 · Lượt 3 — registry kênh và quyền dùng chung

Ngày 2026-11-19. Tiếp nối [DM hợp nhất ở lượt 2](unified-dm.md). Hoàn thành phần registry, discovery và gửi theo UUID của UX3.1b; phần timeline v2/cursor và realtime ở UX3.9 vẫn đang chờ.

Cập nhật sau lượt này: [lượt 4](opaque-timeline.md) đã triển khai timeline/changes HTTP v2 và chuyển frontend sang cursor mã hóa. Delivery/realtime chung và ngừng numeric legacy vẫn còn tiếp tục; các kết quả bên dưới ghi lại phạm vi của lượt 3.

## Thay đổi trong app

- Mỗi phòng có registry `room_channels`: một general, một Thông báo, một Vote, một DM cho từng người và một kênh cho từng nhãn đội trong phạm vi phòng. UUID được lưu trong DB, giữ nguyên qua reload, thay pha, đổi role/phe và rời/vào lại. Phòng ván mới có UUID riêng. Nhãn đội khác tạo identity khác; đổi tên một identity hiện có chưa có trong đợt này.
- Registry được ghi cùng transaction tạo phòng/thành viên/role/bắt đầu ván/đổi role hoặc đội. Unique constraint chống trùng. GET chỉ đọc, không thêm channel, không phát sự kiện hoặc tăng unread. Role/snapshot/tin legacy được backfill bằng migration.
- `GET /api/v2/rooms/{room_id}/channels` trả các kênh actor được xem, tên, UUID, capability và lý do chỉ đọc. Trong trận player chỉ nhận DM của mình và đội hiện tại/đội có tin từng được nhận. Host và người từng tham gia sau trận mở lại được lịch sử theo chính sách hiện có.
- Sidebar và composer dùng chung `ChannelProvider`: một vòng polling quyền mỗi 6 giây, có gộp yêu cầu đang chạy. Mỗi lượt lấy catalog v2 và context legacy song song; context legacy vẫn phục vụ slow mode/cài đặt/unread trong giai đoạn chuyển đổi. Không còn hai vòng polling context riêng ở RoomLayout và ChatPanel. Invalidation WebSocket và đổi version gọi cùng hàm refresh.
- Composer lấy quyền gửi/lý do khóa từ catalog thay vì tự suy lại bằng các cờ riêng. Lỗi tải quyền xóa snapshot đang dùng, ẩn nút gửi và nội dung kênh đang cache; cập nhật phòng lấy lại được dữ liệu. Nháp chưa gửi vẫn được giữ.
- Chat đọc bằng UUID đã xác thực qua `channel_id` trên `/chat/timeline`; URL bookmark theo tên đội/người nhận vẫn hoạt động. Server đối chiếu selector với UUID, không để trộn bộ lọc của hai kênh.
- Gửi dùng `POST /api/v2/rooms/{room_id}/channels/{channel_id}/messages` với **text + client_request_id**. Server xác định actor/phe/người nhận, trả ID nguồn và cờ repeated; response mới không cần global event sequence. Biết UUID không cấp quyền đọc/gửi; UUID khác phòng hoặc không được nhìn thấy bị từ chối.
- Kiểm tra quyền và gửi giữ cùng room lock, tải lại trạng thái sau khi nhận khóa. Nếu người chơi chuyển đội trong lúc màn hình cũ vẫn mở, gửi vào UUID đội cũ bị chặn, không tự chuyển tin sang đội mới. Lệnh chờ khóa khi chuyển ngày sang đêm cũng xét lại pha trước khi ghi tin.
- Retry một lệnh đã commit vẫn được xác nhận khi kênh chuyển chỉ đọc. Cùng ID khác nội dung/kênh bị từ chối. Client giữ được pending ID của payload legacy tương ứng khi nâng sang UUID, gồm trường hợp trước đó đã lưu tin nhưng mất ACK.
- Link `#chat/team` không có tên đội dùng identity đội hiện tại; cache/draft không nhập lịch sử của đội cũ vào đội mới. Header/sidebar nhận đúng đội hiện tại.

Thông báo và Vote vẫn mở công cụ riêng hiện có. Capability `send` ở đây là gửi chat thường; hai kênh đặc biệt không nhận lệnh gửi text thông thường qua endpoint mới. Reaction giữ `false` cho đến UX3.6. `submit_request` phản ánh quy tắc role request hiện có; việc đưa request thành item DM thuộc UX3.11.

## Migration và cập nhật

Migration mới: **`0029_room_channels`**. Backfill kênh từ phòng, thành viên, role template, snapshot, team override và lịch sử chat; nhãn trùng giữa các nguồn chỉ tạo một identity. Không sao chép/xóa tin cũ, không thêm event cho backfill. Đã áp dụng trên DB thử và `alembic check` khớp models.

Dừng API/worker cũ, cấu hình đúng `DATABASE_URL`, rồi chạy:

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
```

Khởi động lại API/worker và dùng frontend mới. Lượt này chỉ áp dụng migration vào DB thử local port 55432, không vào DB phòng thật. Downgrade xóa registry và lần nâng lại tạo UUID mới; không dùng downgrade để quay lui một app đang phục vụ người chơi.

## Kiểm tra

Chromium, frontend production build, API 8026/preview 4176 và PostgreSQL **14** riêng ở port 55432/database `ma_soi_ux2`, schema head 0029. Không bật Redis trong lượt browser; feed PostgreSQL được dùng. Fixture được tạo và dọn theo ID, không thao tác DB phòng thật.

| Bộ kiểm tra | Kết quả |
| --- | --- |
| TypeScript + Vite | Build đạt |
| `npm test --prefix frontend` | Hai file navigation/presentation/emoji/pending ID đạt; có retry legacy → UUID |
| API/WS/role/kick/transcript, gồm `test_channels.py` | 23 test đạt |
| [Browser registry](channel-browser-results.json) | 11 nhóm đạt, không page error |
| [UX3 lượt 1 chạy lại](browser-results.json) | 17 nhóm đạt |
| [UX2 chạy lại](../ux-2/browser-results.json) | 14 nhóm đạt |
| [Migration registry](channel-migration-results.json) | 5 nhóm đạt: backfill đủ nguồn, dedup, giữ nguồn, downgrade/re-upgrade |
| `alembic -c backend/alembic.ini check` trên DB thử | Không có chênh lệch schema/models |
| `git diff --check` | Đạt |

API mới kiểm tra discovery không tăng version/số channel, UUID ổn định, không lộ DM/đội khác, ID sai phòng, bộ lọc không thuộc kênh, quyền theo ngày/đêm/mute/chết, đội cũ, sau trận, retry legacy/current và rollback registry khi source transaction thất bại. Có test giữ room lock để một request đã qua kiểm tra auth phải chờ chuyển sang đêm trước khi được phép gửi.

Browser kiểm tra hai phía DM/phân trang/sửa/xóa/unread/người đã rời/đội cũ như lượt trước, thêm đo lượt HTTP polling quyền, lỗi dịch vụ quyền rồi hồi phục, giữ catalog cũ trên client khi đổi đội để thử gửi sai kênh, và link đội mặc định không lẫn cache. Các phép đo này không thay thế thử tải 500 người.

## Còn tiếp tục

- **UX3.1b/UX3.9:** timeline/changes v2 với opaque cursor và delivery theo người nhận, bỏ metadata numeric ở đường legacy, dùng một room realtime chung. Lượt này **chưa** loại bỏ numeric cursor, event ID/sequence trong DTO legacy và feed hiện có; không đánh dấu đóng các mục audit metadata tương ứng.
- **UX3.10b:** nguồn capability dùng chung đã có, nhưng đồng bộ policy epoch/cache với delivery mới vẫn chờ UX3.9.
- **UX3.6/7b/8/11:** reaction, restore vị trí qua unmount/virtualization và request object trong DM chưa hoàn thành. Chưa đánh dấu toàn bộ UX3 hay cổng F/G đạt.

## Chạy lại

Chuẩn bị DB thử/API/preview theo [hướng dẫn lượt 1](verification.md#chạy-lại), áp dụng migration head và build frontend:

```bash
# DATABASE_URL trỏ đúng DB thử local đã nêu:
.venv/bin/python scripts/check_ux3_channels.py
.venv/bin/python scripts/check_ux3_migration.py --registry
```

Script migration tạo một database tạm tên ngẫu nhiên trên cùng cụm thử; kiểm tra dữ liệu pre-migration, nâng/hạ/nâng rồi xóa đúng DB tạm. Cần quyền tạo DB trên cụm thử. Các script từ chối host/cổng/tên DB nền khác mẫu thử.
