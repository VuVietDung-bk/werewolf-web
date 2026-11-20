# UX3 · Lượt 2 — một lịch sử DM cho cả hai phía

Cập nhật tiếp theo: [lượt 3 — registry và capability kênh](channel-registry.md). Nội dung bên dưới ghi phạm vi lượt 2.

Ngày 2026-11-19. Hoàn thành **UX3.2** và phần hợp nhất nguồn tin của UX3.1. Tiếp nối [timeline/composer/emoji ở lượt 1](verification.md). Toàn bộ UX3 vẫn đang triển khai.

## Hành vi thay đổi

- Tin từ **Bàn quản trò** (`PrivateMessage`) và tin hai chiều trong **Chat** (`ChatMessage`) được API ghép vào một lịch sử DM, áp dụng bộ lọc quyền trước khi phân trang. Quản trò và người nhận xem cùng thứ tự, cùng ID nguồn; không tạo bản sao của tin trong bảng khác.
- ChatPanel dùng `GET /api/rooms/{room_id}/chat/timeline` cho general, đội và DM. Trang mặc định 30, tối đa 50 tin; tải cũ/tải mới/cập nhật đều dùng cùng nguồn. Đã bỏ lượt tải toàn bộ `/game/private-messages` mỗi 8 giây và cách ghép hộp thư riêng chỉ ở phía người chơi.
- Mỗi item có `source_kind` (`chat` hoặc `private_message`). React/dedup dùng khóa gồm nguồn + ID; editor dùng event nguồn thật. Hai bảng có UUID trùng nhau hoặc timestamp bằng nhau vẫn có đủ hai tin, sắp theo thứ tự commit của phòng.
- Quản trò sửa/xóa cả tin gửi từ Bàn quản trò ngay tại menu của DM. Thay đổi commit cùng vị trí cập nhật và sự kiện `chat.revised`; WebSocket chỉ báo cho quản trò và đúng người nhận. Lỗi ghi sự kiện làm rollback cả revision/vị trí; retry dùng lại mã lệnh được phép thành công.
- Tin một chiều tham gia cùng badge chưa đọc của DM. Chat context chỉ trả activity thuộc quyền đọc; xóa tin bỏ nó khỏi activity đang hiển thị. Khi xem một DM sau trận, không đánh dấu các DM khác đã đọc.
- Role bị động vẫn không tự mở chat. Khi quản trò mở bằng tin một chiều, người chơi được trả lời nếu cấu hình `reply_allowed` cho phép; quyền pha/mute/slow mode vẫn do API hiện có xử lý.
- `/chat/context` bổ sung `dm_participants` cho quản trò và người từng tham gia sau trận, kể cả người đã rời/kick. Sidebar có nhãn “đã rời”, giữ đường mở lịch sử; quản trò không gửi tin mới cho người đã rời. Người chơi trong trận không nhận danh sách DM của người khác.
- Sau trận, danh sách đội dùng tất cả đội có lịch sử trong phòng; người từng tham gia được đọc cả DM của người khác, kể cả người đã rời, cùng bản gốc/sửa/xóa. Người ngoài phòng vẫn bị từ chối.
- Đội cũ chỉ hiện tin có người chơi trong recipient snapshot lúc gửi; vào đội mới không mở tin cũ, rời đội không nhận tin gửi tiếp. Người đã rời phòng được loại khỏi roster nhận tin mới của đội.
- Bổ sung pha/ngày cho tin một chiều **mới**. Tin legacy chưa có metadata pha giữ giá trị trống; không phỏng đoán pha từ trạng thái phòng hiện tại.
- Khi chạy regression trên PostgreSQL đặt múi giờ Bangkok, phát hiện transcript xuất offset DB thay vì UTC theo hợp đồng sẵn có. Đã chuẩn hóa `created_at`/`ended_at` của DTO transcript sang UTC; không đổi payload và thời gian gốc trong DB.

## Dữ liệu và triển khai

Migration mới **`0028_private_message_positions`** thêm `sequence`/`updated_sequence` và hai index cho `private_messages`. Backfill từ sự kiện gốc và revision cuối bằng truy vấn tổng hợp; giữ nguyên ID, nội dung, timestamp và lịch sử. Chưa xóa bảng/endpoint legacy. `/chat/messages` vẫn đọc riêng ChatMessage và giữ đường gửi cũ để client cũ không tự nhận thêm một bản tin riêng.

Trang và high-water mark lấy trong **cùng một câu SQL/snapshot**, kể cả trang rỗng hoặc hội thoại chỉ còn tin đã xóa. Một commit mới/sửa/xóa đua lượt đọc sẽ thuộc snapshot hiện tại hoặc lượt changes tiếp theo; không lấy cursor mới hơn dữ liệu trang bằng một query riêng.

Khi cập nhật app đang chạy, dừng API/worker cũ, dùng đúng `DATABASE_URL` của môi trường đó rồi chạy:

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
```

Sau đó khởi động lại API/worker từ mã mới. Phiên kiểm tra này đã áp dụng head trên **DB thử riêng**; không trỏ migration vào DB phòng thật. Không chạy downgrade trên DB đang phục vụ ván đấu.

## Bằng chứng kiểm tra

Môi trường: production build React, Chromium, API 8026/preview 4176, PostgreSQL **14** thử riêng ở port 55432/database `ma_soi_ux2`; schema head 0028. Không bật Redis cho browser test, dùng feed PostgreSQL hiện có. Các script dọn đúng tài khoản/phòng giả của mình.

| Kiểm tra | Kết quả |
| --- | --- |
| TypeScript + Vite build | Đạt |
| `npm test --prefix frontend` | Hai file navigation/presentation/emoji/pending ID đạt |
| API/WS/transcript regression, 10 file liên quan | 19 passed |
| [Browser DM mới](dm-browser-results.json) | 7 nhóm đạt, không page error |
| [UX3 lượt 1 chạy lại](browser-results.json) | 17 nhóm đạt |
| [UX2 chạy lại](../ux-2/browser-results.json) | 14 nhóm đạt |
| [Migration dữ liệu cũ](migration-results.json) | 5 nhóm đạt; DB tạm riêng tạo/xóa tự động |
| `git diff --check` | Đạt |

Bốn test mới trong `backend/tests/test_chat_timeline.py` kiểm tra:

1. 38 tin xen kẽ qua nhiều trang (before/after), ID trùng giữa bảng, timestamp bằng nhau; quyền host/recipient/outsider, bộ lọc sai; activity/cursor của A không đổi vì tin riêng/ghi chú của B; cập nhật/xóa một tin cũ ngoài trang mới nhất; transcript mở rộng và các bản gốc/sửa/xóa.
2. Đổi đội/nhận tin theo snapshot, chỉ đọc đội cũ; giữ DM đã kick cho host và sau trận; chặn gửi mới cho người đã rời.
3. Frame WebSocket tạo/sửa/xóa PrivateMessage chỉ đến host/người nhận; frame không chứa nội dung kín, người khác không nhận invalidation đó.
4. Tiêm lỗi khi ghi delivery sửa tin: rollback revision và vị trí cập nhật; retry cùng ID thành công.

Browser kiểm tra cả menu Sửa/Xóa thật, role bị động mở reply, danh sách DM đúng quyền, trang đầu 30/tải cũ tổng 38, badge chưa đọc, đội cũ, người đã rời và transcript sau trận. [Ảnh host](screenshots/unified-dm-host.png), [ảnh người nhận](screenshots/unified-dm-player.png), [quyền đội mở rộng sau trận](screenshots/unified-dm-postgame.png).

## Phần còn lại

Chưa hoàn thành UX3.1: registry kênh UUID/capabilities và endpoint v2 theo hợp đồng UX1. Lượt này dùng context/selectors hiện có và một đường đọc timeline chuyển tiếp; **cursor vẫn là số**, chưa thay room feed bằng delivery/opaque cursor của UX3.9 và chưa đóng các mục audit metadata liên quan. Không coi một scoped max là opaque cursor.

Reaction (UX3.6), restore/virtualization dài (UX3.7b/UX3.8), realtime chung (UX3.9), trạng thái quyền thống nhất (UX3.10b) và request object trong DM (UX3.11) tiếp tục ở các lượt sau. Chưa đo tải 500 người hay đánh dấu Kiểm tra F/G hoàn thành cho UI mới.

## Chạy lại

Chuẩn bị API/preview/DB thử theo [hướng dẫn lượt 1](verification.md#chạy-lại), áp dụng migration head, rồi:

```bash
# DATABASE_URL phải trỏ đúng DB thử local đã nêu:
.venv/bin/python scripts/check_ux3_dm.py
.venv/bin/python scripts/check_ux3_migration.py
```

Script migration tạo một DB tạm tên ngẫu nhiên trên cùng PostgreSQL thử, nâng đến 0027, nạp fixture tin cũ, nâng 0028, kiểm tra, hạ 0027 rồi nâng lại; cuối cùng xóa đúng DB tạm. Cần quyền tạo DB trên cụm thử. Hai script từ chối host/cổng/tên DB nền khác mẫu thử.
