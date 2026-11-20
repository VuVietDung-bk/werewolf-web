# UX1 · Hợp đồng dữ liệu và chuyển đổi

Đề xuất triển khai cho UX2–UX6, chưa phải endpoint/schema đã tồn tại. Kiểu bên dưới dùng UUID/ISO-8601 UTC. Client không được tự khai quyền host, role, audience hoặc mode phiếu; server suy từ phiên/phòng/vòng. Đặc tả hành vi ở [product-spec.md](product-spec.md).

## 1. Kênh và timeline

```ts
type ChannelRef = {
  id: string; // UUID ổn định, không lấy team label/client ID làm quyền
  kind: 'announcement' | 'vote' | 'common' | 'team' | 'moderator_dm';
  name: string;
  capabilities: { read: boolean; send: boolean; react: boolean; submit_request: boolean };
  readonly_reason: string | null;
  unread: boolean; // chỉ delivery người này được xem
};
type SourceRef = {
  kind: 'chat' | 'private_message' | 'role_request' | 'announcement' | 'vote_public';
  id: string;
};
type TimelineItem = {
  id: string; // ID nguồn hoặc projection ổn định, dedup không dựa text/time
  source: SourceRef;
  type: 'message' | 'role_request' | 'vote_activity' | 'phase_marker';
  author: { id: string; name: string; is_host: boolean } | null;
  created_at: string;
  edited_at: string | null;
  text: string;
  deleted: boolean;
  actions: { edit: boolean; delete: boolean; react: boolean };
  reactions: { emoji: string; count: number; mine: boolean }[];
  request?: { id: string; status: string; reply_text: string | null };
  ballot?: { vote_round_id: string; voter_id: string; choice: 'target' | 'skip' | 'withdraw'; target_id: string | null };
};
type TimelinePage = {
  items: TimelineItem[]; // thứ tự server, client không sort bằng token/sequence
  next_before: string | null;
  next_changes: string; // token snapshot nhất quán với items
};
```

`is_host` lấy từ owner thật; tên “Quản trò” của người chơi không sinh badge. Không có global sequence, event version, role thật, recipient IDs hoặc request link tới hành động kín trong DTO player. Khả năng hiện nút không thay quyền server. Chi tiết request của player không mang role/link của người khác. Sau trận dùng cùng nguồn có expanded access theo consent; token trong trận không được dùng thay quyền sau trận hoặc ngược lại.

Kênh DM keyed `(room_id, participant_user_id)` với unique constraint, không cho client tạo cặp player-player. Team channel keyed định danh team server, nhãn vẫn custom; đổi tên không đổi ID. Read membership theo người nhận tại lúc gửi, không cho thành viên mới tải tin cũ. Player có thể thấy kênh đội cũ chỉ đọc nếu có tin họ từng nhận. Host thấy các kênh trong phòng; player không thấy tên/đếm số kênh bị ẩn.

Để triển khai ChannelRef mà không rewrite dữ liệu ngay: thêm registry `room_channels` (room, kind, participant/team identity); adapter đọc `ChatMessage`, `PrivateMessage`, `RoleRequest`, thông báo cũ vào một DTO. Có constraint mỗi room có đúng một common/announcement/vote, một DM cho mỗi participant, một channel cho mỗi team identity. Khi tạo channel từ dữ liệu cũ không phát tin mới hoặc unread mới. Với role snapshot có team label, tạo identity theo label hiện tại trong phạm vi phòng; rename team về sau cần thao tác riêng không nhập nhằng label.

## 2. Nguồn chuẩn và tương thích

| UI mới | Nguồn hiện có | Thay đổi dự kiến |
| --- | --- | --- |
| general/team/DM hai chiều | `ChatMessage`, `/chat/messages`, `/chat/context` | Lọc bằng ChannelRef server, DTO/cursor v2, giữ permission helpers |
| DM một chiều từ bảng host | `PrivateMessage`, `/game/private-messages` | Hợp nhất timeline DM bằng SourceRef; sửa/xóa dùng source thật |
| Request và phản hồi trong DM | `RoleRequest`, `/role-requests` | Adapter tạo một item có trạng thái, cập nhật item khi review/withdraw, không duplicate sang ChatMessage |
| Thông báo | custom announcement, death/revival, vote publications, public corrections + revisions | Adapter chỉ render text hiện tại; giữ gốc/revisions trong transcript; đổi trạng thái không bắt tạo tin |
| Lịch chờ | `ScheduledAnnouncement` | Thêm version/sửa lịch; finish ghi hủy; lịch thuộc ván, không chuyển sang successor |
| Phiếu | `VoteRound`, `VoteBallot`, GameEvent | Thêm visibility và projection public tối thiểu; không nới quyền review host |
| Emoji reaction | Chưa có | Bảng reaction hiện tại + event/revision; liên kết source đã xác thực cùng phòng |

Thông báo cũ có `public_cause`, thông tin nạn nhân/role đã công bố được dựng thành text một lần theo chính sách public của bản gốc; không mang linked private payload vào DTO. Không tạo hiệu ứng trong adapter/read path. Sửa/xóa công khai vẫn là tombstone/revision; source event tồn tại trong transcript. Không xóa bảng legacy trong UX1–UX4.

## 3. Các endpoint dự kiến

Prefix đề xuất `/api/v2/rooms/{room_id}` để chuyển schema tường minh. Quyền nền dùng lại auth/member/owner của API hiện tại.

| Giao diện | Đầu vào / đầu ra tối thiểu | Kiểm tra chính |
| --- | --- | --- |
| `GET /channels` | ChannelRef[] + opaque `sync_cursor` | Lọc server theo actor; không trả kênh kín |
| `GET /channels/{id}/timeline?before=token&limit=50` | TimelinePage, limit 1–50 | Token scope/user/room/channel; không vừa before vừa changes |
| `GET /channels/{id}/changes?cursor=token&limit=50` | Upsert/tombstone/reaction + next cursor | Giữ update tin cũ; không bỏ tombstone |
| `POST /channels/{id}/messages` | `{client_request_id, text}` → item/ack | common/team/DM: role/pha/mute/slow; announcement chỉ host; vote từ chối |
| `POST /channels/{id}/read` | `{visible_item_id}` | Chỉ cập nhật delivered item đã được xem, không nhận sequence tự khai |
| `POST /role-requests` | Input hiện có | Form request riêng; không tự chuyển tin text thành request |
| `PUT/DELETE /items/{source_kind}/{source_id}/reactions` | `{client_request_id, emoji}` | Actor thực, quyền đọc, nguồn và phase hợp lệ, rate limit; unique source+actor+emoji |
| `POST /announcements` | `{client_request_id, text, publish_timing}` | Host; text 1–2000 sau trim; immediate từ sảnh, next_phase chỉ playing |
| `PATCH /announcements/scheduled/{id}` | `{client_request_id, expected_version, text}` | Host; cùng room, còn pending, khóa/version; trả 409 nếu đã phát |
| `POST /announcements/scheduled/{id}/cancel` | `{client_request_id, expected_version}` | Không yêu cầu lý do nhập cho tin văn bản; giữ audit |
| `POST /votes` | `{client_request_id, visibility, countdown_seconds}` | Host/day/playing; default secret, một vòng open |
| `POST /votes/{id}/ballots` | Input hiện có: target/skip/withdraw | Không nhận visibility/actor; validate vòng, public life, deadline sau lock |
| `GET /votes/{id}/activity` | Projection public hoặc own-only secret, cursor opaque | Không mở host review; secret không có count/tally người khác |
| `WS /live?cursor=token` | ready/invalidation theo quyền | Một kết nối cho cả room; phiên, Origin, membership; replay bounded |

Sửa/xóa tin tiếp tục dùng content revision service với SourceRef hợp lệ, không đưa hidden source event ra client chỉ để làm nút sửa. Error chuẩn: 401 hết phiên, 403 mất quyền, 404 đối tượng không tồn tại/không được nhìn thấy tùy policy hiện có, 409 state/version/idempotency conflict, 422 input không hợp lệ, 429 + Retry-After. Lỗi không chứa tên kênh/actor/payload không được xem.

Lệnh cùng `(room_id, actor_user_id, client_request_id)` cùng canonical payload trả cùng ack; mã được actor khác dùng không được đọc lại response gốc. Không chỉ dedup client. Commit ghi source + event + projection/delivery + outbox trong một transaction; rollback tất cả nếu một phần thất bại.

## 4. Cursor/replay không lộ sequence

### Quyết định

Giữ sequence toàn phòng **ở server** và transcript sau trận. Thêm delivery projection cho người nhận, mang `recipient_user_id`, `source_event_id`, `delivery_position` tăng riêng theo người nhận trong phòng, cùng channel refs được phép invalidation. Sự kiện kín của B không tạo delivery cho A. `Room.public_version` cũng chỉ tăng khi một thay đổi thực sự được công bố. Server WS replay delivery chứ không quét/trả sequence toàn phòng cho client.

Cursor là **token mã hóa AEAD ngẫu nhiên**, không phải base64 hoặc HMAC bọc JSON có sequence đọc được. Nội dung mã hóa gồm version, room, user, channel/scope, policy epoch riêng theo quyền người nhận, internal boundary, issued/expiry; kiểm tra associated data/purpose. Mỗi loại timeline/changes/live có purpose khác. Chọn thư viện AEAD có sẵn/được duyệt ở UX3; khóa server riêng được cấu hình bằng secret, key-id/rotation, không dùng cookie token làm khóa. Không phát triển cipher tự chế. Token có độ dài cố định cho mỗi purpose, giới hạn chiều dài đầu vào trước parse. Không in token vào access/error log.

Token trả lại y nguyên khi cùng boundary và cùng policy epoch, không reissue vì sự kiện không liên quan. Hết hạn dẫn `resync_required` thay vì thông báo có hoạt động kín. Không ép client so sánh token, Math.max(token) hoặc biến token thành số. Tin hiển thị theo thứ tự mảng từ server và liên kết item ID; dùng delivery boundary nội bộ để reconcile, không trả sortable global key cho player.

Policy epoch cũng scoped theo người nhận: không tăng vì host sửa ghi chú hoặc B gửi phiếu secret. Epoch dùng cho A chỉ thay khi quyền A thực sự đổi; không đưa room-global version vào lỗi, ETag hoặc response header.

### Cách đồng bộ

1. Server lấy high-water mark delivery của user trong snapshot transaction. Trả các trang nguồn được phép với boundary cố định; changes sau boundary nhận cả edit/delete xảy ra sau snapshot. Không tính MAX trước query rồi vô tình bỏ qua một commit.
2. WebSocket ready trả opaque live cursor + policy epoch; frame chỉ gồm `type`, opaque delivery ID, scope/channel IDs được phép và cursor tiếp nối. Không có global `sequence`, payload hoặc số lượng sự kiện đã skip.
3. Client nhận invalidation chỉ gọi API của kênh đang mở; kênh khác chỉ cập nhật unread được phép. Các frame trùng delivery ID không xử lý lại. Request lifecycle có thể chỉ invalidation DM và queue host.
4. Khi nối lại, token decode và current auth độc lập, truy vấn delivery sau boundary, lọc lại source theo quyền hiện tại. Thu hồi phiên/kick đóng WS; payload queued cũng bị kiểm tra trước khi gửi.
5. Đổi đội: tin cũ còn quyền đọc theo recipient snapshot, tin mới đội cũ không tạo delivery; đội mới không mở lịch sử cũ. Policy epoch thay đổi thì resync ChannelRef và cache có scope, không phát tên đội bị ẩn.
6. Finished mở quyền transcript theo consent, tăng policy epoch, client clear scoped pages và resync lịch sử sau trận. Guest khác không dùng token cũ để mở dữ liệu.

Unread là read position trong delivery của channel + user, không trừ số sequence toàn phòng. Reaction/edit chỉ tạo delivery đúng audience, không đánh dấu toàn phòng unread. Host queue có cursor riêng; player không thấy số request của người khác.

### Danh sách loại bỏ chỗ lộ

| Hiện tại | Cần thay cho player |
| --- | --- |
| Room output version | Chỉ version công khai; không ép thành global khi component mới cần refresh |
| Chat item sequence/updated_sequence, next-before/after/update và room_cursor | Opaque page/change cursors; item ID + thứ tự server |
| PrivateMessage.sequence | Unified DM DTO, không số global |
| RoleRequest.sequence/updated_sequence/page cursor | Request item + private changes cursor |
| ChatContext.activity.sequence | Scoped unread bool/read cursor |
| Announcement/correction/death notice source_sequence | Public text/projection ID; không global source ordinal |
| WS ready.cursor + game.event.sequence và after_sequence | Opaque token + delivery ID; legacy numeric replay retired |
| vote/role linkable actions | Host-only giữ khi cần; không gửi liên kết private cho player |

Cập nhật UI chưa đủ: khi chuyển API, endpoint legacy còn lộ metadata phải đổi DTO hoặc trả 426 theo phiên bản client đã công bố. Không để client cũ bypass bằng cách gọi path cũ. Rollout frontend/backend đồng bộ; có cửa sổ maintenance nếu cần, không cho downgrade quay lại protocol rò rỉ. Proxy log query token phải redact tương tự WS log hiện có.

## 5. Ma trận quyền tối thiểu

| Dữ liệu/hành động | Ngoài phòng | Thành viên | Host | Sau trận thành viên consent |
| --- | --- | --- | --- | --- |
| general / thông báo đã đăng | Không | Đọc; gửi theo policy / host-only notice | Đọc/gửi | Đọc, không ghi/react |
| team | Không | Recipient snapshot + quyền hiện tại cho tin mới | Đọc/gửi theo công cụ host | Toàn lịch sử |
| DM một người + request | Không | Chỉ DM/request mình | Đúng DM được chọn | Toàn lịch sử |
| reaction | Không | Tin đã đọc, kênh cho phép, phase cho phép | Cũng chỉ sửa reaction của mình | Đọc |
| scheduled | Không | Không | Xem/sửa/hủy pending | Xem trong transcript |
| vote secret | Không | My ballot only, không count | Mọi phiếu và tally | Mọi lần gửi/đổi/rút |
| vote public | Không | Mọi phiếu đã commit, không nháp | Như member + review kín | Mọi lịch sử |
| role assignment/effects thật | Không | Chỉ mình + phần public của người khác | Mọi người | Mọi người |
| state kill/revive/publish | Không | Không | Có, event/audit | Chỉ đọc |
| queue/moderation/phase | Không | Không | Có | Chỉ đọc |

Sửa policy/mode không thay read authorization bằng cờ client. Quyền đọc kênh không cho quyền sửa mọi nguồn tin. Kick trong trận vẫn giữ transcript access sau trận theo policy hiện hành, nhưng đóng quyền live ngay.

## 6. Migration và public vote projection

- Thêm `VoteRound.visibility` NOT NULL default secret với check public/secret; backfill toàn bộ vòng cũ secret trước khi mở API mới. Có integration test DB cũ có phiếu/revision.
- Public projection có source ballot-event unique, room/vote/voter/target(optional)/choice/time, không copy payload GameEvent. Source event kín vẫn tồn tại; projection và public invalidation commit cùng source, không tạo projection từ vòng secret.
- Read model current vote lấy phiên bản sau cùng mỗi voter; activity giữ mọi mutation được công khai. Retry không thêm dòng; `withdraw` có nghĩa riêng. Danh sách public không có role, reason host hoặc true is_alive.
- Lock room trước vote/ballot, kiểm tra time/state dưới lock. Close thắng thì ballot trả 409; ballot thắng trước deadline thì ghi source+projection rồi close. Mở vòng thứ hai sau đó không tái sử dụng stream của vòng đầu.
- Lịch notice thêm version, normalized content; message-only publication không chạm PlayerState. Public state command nhận `announcement_text?: string`, trống không tạo text; scheduled status cũ vẫn có policy riêng, adapter không biến nó thành chat command.
- Thêm registry/delivery/reaction theo migration riêng để review. Backfill delivery từ source và historical recipient snapshot; không giả vờ biết audience từ đội hiện tại. Những nguồn thiếu chứng cứ recipient cần kế hoạch riêng, không cấp public mặc định.
- Rollback UI không xóa phiếu public/reaction/source; schema additive giữ projection, khóa write nếu client cũ không hiểu. Một khi đã phát public không thể “rollback bí mật”; lịch sử công khai vẫn còn. Backup/restore và schema downgrade test dùng DB giả.

## 7. Tình huống nghiệm thu bắt buộc cho các bước sau

| ID | Kiểm chứng |
| --- | --- |
| P1 | A đọc general rồi B gửi request/host note/secret ballot: body/cursor/unread/WS của A không thay vì ba hành động kín đó. |
| P2 | Token A dùng bởi B, khác phòng, khác channel/purpose, sửa 1 byte hoặc quá dài: không trả nội dung hay boundary; reload an toàn. |
| P3 | Team join/leave/kick khi có frame đang chờ: không nhận tin mới trái quyền, vẫn đọc đúng tin cũ từng được cấp. |
| P4 | DM ChatMessage/PrivateMessage/request có timestamp trùng: render đúng một lần mỗi source, stable order sau reload/retry. |
| P5 | Snapshot timeline đua edit/delete/create: changes bắt đủ, tombstone không bỏ sót. |
| P6 | Public ballot submit/retry/skip/withdraw/close đồng thời: projection unique và current tally đúng. |
| P7 | Secret mode không đổi sau mở, public endpoint không trả review/tally/phiếu khác, sau trận mới expanded read. |
| P8 | Hẹn notice edit/cancel đua advance/finish: chỉ một kết quả commit, không tin ván cũ rơi sang ván mới. |
| P9 | Reaction đến source trái room/audience, Unicode ghép, retry hai tab: unique và đúng actor. |
| P10 | Client cũ gọi đường numeric cursor sau chuyển đổi: không đọc được số global bằng route legacy. |
| P11 | Restart/replay delivery sau restore: không mất source commit, không phát lại nội dung trái quyền; khóa rotation cho resync an toàn. |
| P12 | Đổi danh tính demo/đăng xuất: nháp, cache, token, unread và socket của người cũ được dọn. |

Đây là bộ tiêu chí thiết kế, chưa phải kết quả test API. Cổng F/G chỉ tick sau khi chạy trên ứng dụng thực.
