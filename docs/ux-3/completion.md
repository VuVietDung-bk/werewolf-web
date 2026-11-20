# UX3 hoàn thành · chat và yêu cầu role trong DM

Ngày 2026-11-20. Hoàn thành phần Chat của khung **Chat / Role / Quản lý**, tiếp nối [lịch sử dài](virtual-history.md). Phạm vi UX3 gồm common/team/DM và request/review trong DM. Timeline/read của Thông báo và Vote public tiếp tục ở **UX4.11**, cùng lúc hai nguồn đó được viết lại; chưa nghiệm thu UX4, F hoặc G.

## Yêu cầu dùng role

- DM hiển thị ba nguồn `ChatMessage`, `PrivateMessage`, `RoleRequest`, sắp theo thứ tự commit. Khóa gồm loại nguồn và UUID, nên UUID hoặc timestamp trùng giữa các bảng không gộp nhầm. Không tạo bản sao request thành tin chat.
- Nút **Yêu cầu dùng role…** mở form riêng, chỉ nhập nội dung tự do. Điều kiện role vẫn ở mô tả; API kiểm tra role chủ động, pha, trạng thái sống và tạm dừng. Chat thường giữ nguyên là tin nhắn; role bị động vẫn có thể trả lời DM quản trò đã mở theo policy riêng.
- Một thẻ request hiện role khi gửi, nội dung, trạng thái và phản hồi quản trò. Người gửi có thể rút khi còn pending/in_review; quản trò mở popup để xét/chấp nhận/từ chối và liên kết thao tác đã làm nếu cần. Request không tự giết, áp hiệu ứng hoặc đổi pha.
- DM và hàng đợi quản trò dùng cùng ID và cập nhật qua provider của phòng. Review/withdraw sửa item hiện có; không nối một tin trùng vào timeline. Thẻ request không có sửa/xóa tin thường hoặc reaction.
- Nháp form và mã retry được giữ qua reload; mất ACK rồi gửi lại không tạo request thứ hai. Các lệnh chờ khóa phòng kiểm tra lại membership/phase: kick/kết thúc thắng trước thì không có mutation mới.
- Sau trận, request và phản hồi mở rộng đúng consent như DM; mọi thao tác khóa. Các lần xét/rút và liên kết vẫn nằm trong transcript; thẻ dẫn tới nhật ký đầy đủ.

## Đã đọc và realtime

Request dùng mốc delivery của lần submit/review/withdraw được nhìn thấy. Timeline trả `read_receipt`, là UUID delivery **của chính actor**, không phải ordinal hay ID event toàn phòng. ACK phải bind đúng actor/phòng/request; dùng receipt của người khác hoặc request khác bị từ chối.

Receipt cũ chỉ ACK tới trạng thái cũ. Phản hồi mới tới trong lúc ACK đang chờ vẫn unread. Observer chỉ ACK khi hàng thực sự xuất hiện trong viewport; dialog hoặc tab ẩn tiếp tục chặn ACK. Đây vẫn là quy ước read-through của kênh: đọc một delivery phía sau sẽ tiến mốc chung qua các delivery trước đó, không phải read receipt từng tin công khai.

Giữ nguyên scopes `channels/role_requests` của các delivery request lịch sử. ChatPanel tiêu thụ cả scope `chat` lẫn `role_requests` theo đúng UUID kênh; không cần viết lại audience/position/epoch hay migration delivery cũ. Request lịch sử dùng mốc đọc sẵn có và có thể làm DM hiện badge Mới nếu lần cập nhật chưa được checkpoint bao phủ.

## Đóng đường đọc numeric của người chơi

| Đường gọi | Hành vi |
| --- | --- |
| `GET /chat/messages`, `/chat/timeline`, `/game/private-messages`, `/role-requests` cũ | Người chơi trước khi ván kết thúc nhận **426 client_upgrade_required**. Kiểm tra phiên/phòng vẫn chạy trước; người ngoài không được đọc. |
| Đọc numeric của quản trò và lịch sử sau trận | Còn phục vụ công cụ quản trò hoặc thành viên có consent sau trận; đây là các actor được xem toàn nhật ký. |
| `/api/rooms/{room}/live` cũ | Kiểm tra Origin/phiên/quyền, rồi đóng **4406**, không phát ready/cursor/event frame. Trình duyệt nhận mã đóng rõ sau handshake. |
| `/chat/context` | Người chơi trong trận nhận `activity: []`; capability/policy vẫn có. Registry tính activity bằng service nội bộ, không công khai ordinal. |
| Thông báo/đính chính công khai hiện tại | Không trả `sequence`/`source_sequence` cho người chơi trong trận. Quản trò và lịch sử sau trận giữ thông tin được phép xem. |
| Timeline/changes/sync/live v2 | Cursor opaque, source-scoped DTO, audience theo người nhận, session/epoch và replay đã kiểm tra ở các lượt trước. |

Các lệnh POST cũ còn dùng service quyền/idempotency hiện có và chỉ trả UUID ACK; không phải đường đọc numeric. Frontend Chat gửi theo UUID kênh v2. Rollout API/frontend đồng bộ trong cửa sổ bảo trì; client cũ phải cập nhật. Không downgrade về giao thức làm lộ global ordinal cho player.

Đây là xử lý phát hiện metadata đã xác nhận trong audit, không phải kết luận mọi ứng viên/tài nguyên/cấu hình production đã được kiểm tra.

## Cấu trúc và vận hành

Đã tách `ChannelList`, `MemberList`, `MessageTimeline`, `MessageRow`, `MessageComposer`, `EmojiPicker` và công cụ request. RoomLayout giữ bố cục, điều hướng, drawer và provider. Chỉ có một room realtime transport, dùng chung qua ba tab.

Head vẫn **0032_message_reactions**, không có schema mới cho request vì dùng bảng/projection sẵn có. Nếu DB còn cũ, dừng writer và chạy:

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/alembic -c backend/alembic.ini check
```

Triển khai lại cả API và frontend, dùng cùng key ring cursor giữa worker. Không áp migration lên DB phòng thật trong lượt này; kiểm thử dùng PostgreSQL14 riêng localhost:55432.

## Kiểm chứng

| Phạm vi | Kết quả |
| --- | --- |
| Backend đầy đủ | **103 test đạt**, gồm 11 ca mới cho request/receipt/protocol/queued kick-finish |
| Frontend | **7 file test đạt**, thêm ca giữ ID/vị trí thẻ request khi trạng thái và receipt đổi |
| Browser request mới | **11 nhóm đạt**, không có page error |
| Hồi quy UX2/UX3 | **94 nhóm đạt**, gồm 16 nhóm lịch sử 10.000 tin |
| Migrations 0028–0032 | **34 kiểm tra đạt** (5 + 5 + 8 + 8 + 8), mỗi script tạo và xóa DB riêng |
| Build/schema | TypeScript + Vite đạt; Alembic check không có thao tác upgrade mới |

Tổng **105 nhóm browser** đạt qua một lượt runner đầy đủ. Ma trận mới thử ba actor, GM và player dùng giao diện thật, reply/withdraw đồng bộ, mất ACK và reload, receipt khi dialog mở, role bị động/sai pha, hậu trận, mobile 320px và old WS không có frame numeric. Test backend có ACK đua review mới, receipt sai actor/request, hai nguồn trùng UUID, request chờ kick/finish và public DTO không lộ thứ tự kín.

 Các test numeric cũ vẫn kiểm tra domain service và feed base qua adapter **chỉ có trong backend/tests**; không được mount vào app.main. Test HTTP/WS thực kiểm tra player nhận 426/4406 và tất cả client hiện tại dùng v2. Không dùng test adapter làm chứng cứ giao thức cũ vẫn hoạt động.

Chạy lại với DB disposable đã migration:

```bash
DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:55432/ma_soi_ux2 APP_ENV=local PYTHONPATH=backend .venv/bin/pytest backend/tests -q
npm test --prefix frontend
DATABASE_URL=postgresql+psycopg://nobody@127.0.0.1:55432/ma_soi_ux2 .venv/bin/python scripts/run_ux3_browser.py
```

Runner tự build/mở/dừng API và preview. Bộ browser gồm requests, lịch sử 10.000 tin, reaction, read ACK, realtime, registry/cursor, chat và khung UX2. Artifact request: [JSON](request-browser-results.json), [sau trận](screenshots/requests-postgame.png), [mobile](screenshots/requests-mobile.png).

## Phần tiếp theo

UX4 làm Thông báo/Vote mới và read model nguồn đặc biệt; UX5 tiếp tục tối ưu Role/Quản lý. F/G gồm kiểm tra chéo toàn luồng, thiết bị thật và đo lại 500 người sau thay đổi. Các mục này còn mở; kết quả headless/local không thay nghiệm thu production.
