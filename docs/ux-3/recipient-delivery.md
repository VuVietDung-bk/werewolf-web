# UX3 · Lượt 5 — delivery theo người nhận và HTTP recovery

Ngày 2026-11-20. Tiếp nối [timeline HTTP v2](opaque-timeline.md). Hoàn thành nền delivery của UX3.9c: lưu người nhận cùng transaction và cung cấp API replay phân trang. Frontend/WebSocket chưa chuyển sang feed này.

Cập nhật sau lượt này: [lượt 6](shared-realtime.md) đã đưa feed vào WebSocket v2/provider frontend dùng chung, có kiểm thử reconnect/thu hồi quyền và hồi quy giao diện. Nội dung dưới đây ghi lại phạm vi lượt 5.

## Mã nguồn và hành vi

`append_event` nay ghi source event, outbox, registry và delivery trong cùng transaction. Mỗi delivery là một invalidation: ID UUID riêng, các khu vực cần tải lại và UUID kênh được phép. Nội dung tin, tên role, kết quả phiếu và payload hành động kín không được copy vào response feed.

Hai bảng mới:

- `room_delivery_streams`: checkpoint và policy epoch riêng cho từng người trong từng phòng.
- `room_deliveries`: source event nội bộ, người nhận, vị trí riêng trong stream, scopes và channel refs. Có unique cho source+người nhận và vị trí+người nhận. Đường ghi ứng dụng chỉ thêm row; không sửa audience cũ khi đổi đội. Xóa phòng theo retention cascade cả stream và delivery.

Room lock có sẵn được giữ trong lúc cấp vị trí. Một sự kiện kín của B không tăng vị trí hay epoch của A. Retry command đã commit không gọi append lần nữa; lỗi ghi delivery làm rollback event/outbox/counter/source cùng transaction. Host nhận các invalidation của phòng; player nhận theo bảng dưới và phải có consent hợp lệ, đang ở phòng tại lúc ghi.

| Nguồn | Người chơi nhận invalidation |
| --- | --- |
| Chat chung / sự kiện public | Thành viên hiện tại |
| Chat đội và sửa/xóa | Giao của thành viên hiện tại với recipient snapshot đã lưu, không suy từ đội hiện tại khi replay |
| DM hai chiều / quản trò gửi riêng | Participant của DM |
| Yêu cầu role / review / rút | Người gửi yêu cầu |
| Kill/revive/effect/đổi role, phe, đội | Người được tác động; ghi chú vẫn host-only |
| Mute / mở chat | Người bị đổi quyền |
| Phiếu secret | Chủ phiếu; không có dữ liệu phiếu trong invalidation |
| Phát role / bắt đầu chọn role | Người được phát / thành viên trong phòng; không trả role hoặc ứng viên trong feed |
| Offer/chốt draft | Người có lượt đó |
| Private kind chưa được khai báo | Host-only, không tự cấp cho user có tên trong payload |

Đổi role/đội tăng epoch của đúng người được tác động. Epoch token còn gắn với lần vào phòng, consent và trạng thái kết thúc trận. Không dùng room-global version hay hoạt động của người khác. Kick chặn HTTP ngay theo quyền hiện có; sau trận thành viên consent được lấy snapshot mở rộng và token trong trận yêu cầu resync.

## API recovery

`GET /api/v2/rooms/{room_id}/sync`

Không có cursor: lấy checkpoint hiện tại, trả `deliveries: []`. Consumer phải mở boundary này **trước** khi lấy snapshots cho UI rồi đọc tiếp từ cursor; lịch sử có sẵn không bị biến thành tin mới/unread.

Có `cursor`: trả tối đa `limit` invalidation theo thứ tự server, `has_more`, `next_cursor`. Mặc định 50, tối đa 100. Consumer drain `has_more` rồi tiếp tục poll/reconnect; khử trùng theo delivery ID. Một delivery có dạng:

```json
{
  "id": "UUID riêng của delivery cho người xem",
  "scopes": ["channels", "chat"],
  "channel_ids": ["UUID kênh người này được phép xem"]
}
```

Response không có sequence/position/source event ID/kind/payload/epoch/recipient list. Source event chỉ là FK nội bộ. Cursor AEAD dài 114 ký tự, dùng key ring đã cấu hình ở lượt 4, gắn phòng/user/purpose `room_live`. Không dùng token timeline/changes thay token recovery được.

Mỗi lần đọc giữ high-water riêng của actor và chỉ lấy row không vượt mốc đó; commit mới sau mốc sẽ còn cho lần kế tiếp. Không có delivery mới thì trả nguyên token đầu vào. Token bị sửa, sai actor/phòng/purpose, hết hạn, đổi epoch hoặc đi trước DB sau restore trả `409 chat_resync_required`. Không phản chiếu token lỗi. GET không tạo stream, delivery, event hay unread.

## Migration và triển khai

Head mới: **0030_room_deliveries**. Dừng các API và tác vụ ghi dữ liệu phòng trong cửa sổ cập nhật để backfill có boundary rõ ràng; không chạy lẫn backend cũ còn ghi sự kiện thiếu delivery. Với đúng `DATABASE_URL` của môi trường cần cập nhật:

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/alembic -c backend/alembic.ini check
```

Sau đó khởi động API mới và worker. Khóa `CHAT_CURSOR_KEYS`/`CHAT_CURSOR_ACTIVE_KEY` dùng theo [hướng dẫn lượt 4](opaque-timeline.md#cursor-và-cấu-hình). Lượt này chỉ áp dụng migration lên DB thử nghiệm riêng; không tự sửa DB ứng dụng đang chạy.

Backfill dùng SQL cố định trong migration, không import logic ứng dụng. Chỉ cấp delivery lịch sử cho player trong khoảng tham gia gần nhất có thể chứng minh bằng `joined_at`/`left_at`, với consent hiện tại hợp lệ. Chat đội vẫn cần recipient snapshot trong sự kiện; thiếu dữ kiện thì không cấp cho player. Không dựng audience từ role/đội hiện tại, không tạo public event, outbox hay thông báo mới.

Các lần tham gia trước một lần rejoin không thể phục dựng đầy đủ từ bảng membership hiện tại, nên không suy đoán delivery cho khoảng đó. Lịch sử nguồn và các API timeline được cấp quyền vẫn giữ nguyên, consumer mới khởi đầu bằng snapshot. Đây là feed invalidation để phục hồi, không phải bản thay thế transcript.

Backfill thêm tối đa một row cho mỗi người nhận mỗi event; cần dự trù dung lượng và đo migration trên bản sao dữ liệu thực trước production. Kiểm thử downgrade/re-upgrade chỉ chứng minh giữ nguồn gốc: downgrade xóa delivery/checkpoint, làm token feed mất hiệu lực và không phải quy trình rollback khi client mới đang chạy.

## Kiểm chứng

| Phạm vi | Kết quả |
| --- | --- |
| Toàn bộ backend | 63 test đạt, gồm 6 test delivery mới |
| Bộ API/cursor/chat/live/transcript liên quan | 35 test đạt trong bộ trên |
| [Migration lịch sử](delivery-migration-results.json) | 8 kiểm tra đạt với 25 event, DB riêng được xóa sau kiểm thử |
| Schema | Alembic check không phát hiện chênh lệch ORM/migration |
| [Browser hồi quy đường ghi](delivery-browser-results.json) | 15 nhóm đạt, không có page error; UI vẫn dùng WS cũ |
| Build frontend | TypeScript + Vite đạt |

Test mới xác minh: B gửi DM/phiếu/request/note không đổi cursor A; unknown private event không tự đến A; GET không ghi DB; phân trang và replay không trùng; 4 retry đồng thời chỉ một source; sửa/xóa dùng đúng DM UUID; đổi đội không nhận tin mới đội cũ; kick/finish; rollback sau khi đã ghi projection; token trái scope/limit/restore/logout; commit sau high-water không bị bỏ sót. Migration kiểm tra thêm thành viên vào muộn/rời sớm, thiếu recipient snapshot, scopes/UUID khớp hợp đồng, constraint unique và cascade retention.

Chạy lại trên DB thử nghiệm đã migration:

```bash
DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:55432/ma_soi_ux2 APP_ENV=local PYTHONPATH=backend .venv/bin/pytest backend/tests -q
DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:55432/ma_soi_ux2 APP_ENV=local PYTHONPATH=backend .venv/bin/python scripts/check_ux3_delivery_migration.py
```

Browser hồi quy dùng `scripts/check_ux3_channels.py --opaque` với API 8026, frontend preview 4176 và cùng DB thử nghiệm theo hướng dẫn lượt 4. Nó kiểm tra tương thích đường chat đang chạy, không chứng minh frontend đã tiêu thụ `/sync`.

## Lượt tiếp theo

Chuyển WebSocket sang delivery replay, kiểm tra lại session/membership trước khi gửi frame, cùng cơ chế resync/epoch. Sau đó tạo một provider realtime dùng chung ở cấp phòng, bỏ socket riêng ở App/ChatPanel, nối polling recovery và dọn socket/token khi đổi user/phòng. Vị trí đọc server/viewport ACK vẫn chưa có.

Các endpoint numeric legacy và metadata ở context/WS/request/thông báo còn mở; chưa đóng audit metadata, chưa hoàn thành toàn UX3.9 hay cổng F/G. Chưa đo lại mục tiêu 500 người sau khi thêm chi phí ghi projection.
