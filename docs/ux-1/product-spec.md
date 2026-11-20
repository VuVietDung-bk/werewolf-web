# UX1 · Đặc tả trải nghiệm ba tab

Ngày: 2026-11-19. Phạm vi: thiết kế và prototype, chưa thay ứng dụng đang chạy. Đọc cùng [wireframe tương tác](wireframes.html) và [hợp đồng dữ liệu](data-contract.md). Quyết định trong tài liệu này thay các mô tả bố cục cũ tại `docs/step-1/`; dữ liệu và các bằng chứng chạy bản cũ vẫn được giữ.

## 1. Quyết định thiết kế

- Trong phòng luôn có đúng **Chat / Role / Quản lý**. Trang vào làng nằm ngoài phòng, truy cập qua menu tên phòng. Không có tab Sảnh, Vote, Thông báo, Yêu cầu hay Nhật ký riêng trên thanh ngang.
- Chat có ba vùng: kênh, nội dung và thành viên; thứ tự kênh: Thông báo → Vote → general → nhóm riêng. Riêng gồm đội và từng cuộc trò chuyện quản trò–người chơi, không có DM giữa hai người chơi.
- Tin là hàng liền nhau trên nền trung tính, không phải các thẻ viền cách xa. Thông báo dùng cùng renderer tin văn bản. Vote có renderer diễn biến và vùng chốt phiếu, không có ô chat tự do.
- Quản lý hiện cho cả người chơi và quản trò nhưng nội dung theo quyền. Trước trận chứa thông tin phòng/thành viên; khi chơi quản trò có người chơi, yêu cầu, điều hành, cài đặt và lịch tin; sau trận có nhật ký và mở ván mới.
- Role là nơi duy nhất thiết lập bộ role/phát role trước trận. Role gán, hiệu ứng và thao tác trong trận ở drawer người chơi của quản trò. Mô tả mọi role vẫn công khai.
- Bản nháp tin/phiếu ở client chưa phải hành động. Chỉ sau xác nhận lưu từ server mới hiện “đã gửi”; retry dùng cùng request ID. Không tự suy diễn kết quả kỹ năng hoặc thắng thua.

## 2. Bản đồ điều hướng

URL sản phẩm đề xuất: `#chat/general`, `#chat/announcements`, `#chat/vote`, `#chat/<channel_id>`, `#role/catalog`, `#role/distribution`, `#role/mine`, `#management/players`, `#management/requests`, `#management/room`, `#management/game`, `#management/settings`, `#management/scheduled`, `#management/history`. Channel ID thực tế lấy từ server; alias general/announcements/vote ánh xạ kênh cố định trong phòng. Menu phòng đưa về trang vào làng, không tạo tab thứ tư. Back/Forward dùng history trình duyệt, link không hợp lệ trả màn mặc định hợp quyền.

| Route cũ | Vị trí mới |
| --- | --- |
| home | Trang vào làng ngoài RoomLayout |
| lobby | Chat/general mặc định; quản lý thành viên ở Quản lý/Phòng |
| chat, private | Chat/general hoặc DM quản trò nếu link cũ chỉ rõ cuộc trò chuyện |
| dead | Chat/Thông báo |
| vote | Chat/Vote |
| catalog | Role/Danh mục |
| manage | Role/Bộ role cho host trước trận; người chơi chuyển Danh mục |
| distribution | Role/Phát role cho host; Role/của tôi cho người đang chọn |
| assignment | Role/của tôi với người chơi; trước trận host tới Role/Phát role, trong trận tới Quản lý/Người chơi |
| game | Chat/general; trạng thái pha ở header |
| requests | Host: Quản lý/Yêu cầu; player: DM quản trò cuộn tới request |
| control | Quản lý/Người chơi, chỉ host |
| transcript | Quản lý/Nhật ký khi kết thúc; trước kết thúc giải thích chưa mở |

| Trạng thái | Chat | Role | Quản lý |
| --- | --- | --- | --- |
| Sảnh open/closed | general gửi được; Thông báo host gửi ngay; Vote trống; chưa có đội | Host tạo/sửa và phát; player đọc danh mục, chưa có role | Phòng, thành viên/kick host, mở/đóng đăng ký; player rời |
| Drafting | general tiếp tục; lời mời không gửi vào chat chung | Player thấy tối đa 3 lời mời của mình khi tới lượt; host thấy tiến độ, Random hộ | Phòng/ghế, không chỉnh bộ role đã snapshot |
| Assigned | general; chưa cho dùng kỹ năng | Role của tôi hoặc host kiểm tra phân role | Host bắt đầu Ngày 0 khi đủ điều kiện |
| Playing | Pha/quyền gửi hiện tại; hai kênh đặc biệt giữ nguyên vị trí | Host danh mục chỉ đọc; player role mình + danh mục | Host điều hành/requests/player drawer; player thông tin phòng, trợ giúp |
| Finished | Toàn bộ lịch sử được mở theo consent; mọi composer/reaction khóa | Snapshot và phân role sau trận được phép xem | Nhật ký/xuất/ván mới/rời; host không sửa ván đã kết thúc |

## 3. Chat, cuộn và các thao tác

Header chung 52px, header kênh 48px. Desktop cột kênh 232px, thành viên 220px; chiều cao `100dvh`. Nội dung giữa dùng `minmax(0,1fr)`, mọi container cần `min-height:0; min-width:0`. Không cho body cuộn ở workspace. Laptop dưới 1200px ẩn thành viên vào drawer; dưới 760px cả hai cột thành drawer. Mỗi danh sách có scroll riêng. Drawer dùng dialog, focus trap, trả focus về nút mở, Escape/đóng, một drawer tại một thời điểm.

Composer từ 44–120px, lớn hơn thì cuộn bên trong. Enter gửi, Shift+Enter xuống dòng; IME đang compose không gửi. Viewport thay đổi vì bàn phím phải giữ composer trong vùng nhìn thấy. Nháp keyed room+user+channel trong sessionStorage; không lưu tin đã nhận vào localStorage; xóa nháp và cache khi đổi danh tính/đăng xuất/kick. Hết phiên không cho retry bằng danh tính mới một cách tự động.

Nhóm tin cùng tác giả, loại và phạm vi trong 5 phút. Tin đầu có avatar/tên/badge host/timestamp, tin sau chỉ thân tin và timestamp khi focus/hover. Mốc ngày/pha, thông báo hệ thống, request và phiếu ngắt nhóm. Menu mỗi tin không bị mất khi gom. Giữ quyền sửa/xóa hiện có (host với nội dung của host); chưa mở rộng quyền sửa tin người chơi trong đợt này.

| Tình huống cuộn | Hành vi |
| --- | --- |
| Cách đáy ≤80px, nhận tin mới | Theo xuống cuối sau đo chiều cao |
| Đang đọc lịch sử | Giữ item neo + offset; hiện nút “N tin mới · Đến cuối” |
| Vừa gửi chính mình | Đến tin vừa gửi trong kênh đó; không ép đổi kênh khác |
| Tải trang cũ tại đầu | Prepend, khôi phục item neo + offset sau render/ảnh tải; không đặt scrollTop=0 |
| Đổi kênh quay lại | Khôi phục neo/offset theo user+room+channel; chưa xem thì mở cuối |
| Kênh có ≥10.000 tin | Windowed rendering, tối đa khoảng 200 hàng đang mount; cache trang có trần; không fetch tất cả |
| Mất mạng/retry | Giữ text nháp và request ID; hiện retry; realtime và HTTP dedup theo ID |

Emoji picker hỗ trợ Unicode, alias tìm kiếm và gần dùng local; popup có nhãn, focus và Escape. Reaction chỉ trên tin chat/thông báo được đọc, không vote/request trạng thái; khóa sau trận. Chuỗi emoji ghép được coi là một lựa chọn, không cắt theo byte hoặc một code unit. Nội dung luôn là text, không HTML. Prototype minh họa picker nhỏ; bộ emoji đầy đủ và virtualization thuộc UX3.

## 4. Thông báo: gửi như chat

Composer: nội dung + emoji + “Gửi ngay / Pha tiếp theo” + Gửi. Không hỏi lý do, loại sự kiện, người gây ra hay mục tiêu. Không confirm tin thường. Sửa/Xóa ngay trên tin, không âm thầm xóa bản gốc trong transcript. Player chỉ đọc và react khi chưa kết thúc. Sảnh cho gửi ngay; hẹn chỉ khi playing có pha kế tiếp.

Đăng chữ “An chết” **không đổi trạng thái An**. Drawer quản trò có hai thao tác khác nhau: trạng thái thật và công bố trạng thái. Công bố có confirm preview; nội dung thông báo là tùy chọn và có thể bỏ trống. Bỏ thông báo không đồng nghĩa giấu trạng thái đã công bố. Muốn giữ cái chết hoàn toàn kín thì giữ cả trạng thái công khai cũ; không badge/tin hệ thống/ràng buộc vote tiết lộ cái chết thật.

Lịch chờ chỉ host thấy, có sửa/hủy và version chống ghi đè. Lịch giữ thứ tự tạo, phát đúng lần chuyển pha thành công tiếp theo cùng transaction; pause/resume không phải chuyển pha. Finish hủy lịch còn lại bằng sự kiện, không phát vào ván mới. Sửa/hủy đua với chuyển pha: lock room trước lịch; bên commit trước quyết định, bên thua nhận 409 và tải trạng thái thật. Không dùng đồng hồ client để phát tin.

## 5. Vote theo vòng

Default `secret`; host chọn `public` hoặc `secret` khi mở. Mode bất biến sau khi mở, không retroactively lộ phiếu secret. Mở vòng cần playing/day (kể cả Ngày 0)/không pause; timer tùy chọn 10–3600s theo server. Vòng mới có thể dùng mode khác.

Player chọn người sống công khai hoặc Bỏ qua để tạo **nháp**; bấm **Chốt phiếu** mới lưu. Sau chốt vẫn đổi/rút tới lúc đóng. Rút không phải Bỏ qua. Không cần mọi người gửi, host không bỏ phiếu. Người chết kín vẫn xử lý theo trạng thái sống công khai để tránh lộ. Đóng/decide/publish không tự giết hoặc chuyển pha. Kết quả vẫn do host nhập và xác nhận riêng.

| Tình huống | Public | Secret |
| --- | --- | --- |
| Nháp/chuyển ứng viên trước chốt | Không phát | Không phát |
| Chốt chọn người | “An vote Bình”, update current ballot và tally | Chỉ An + host thấy |
| Đổi phiếu | “An đổi từ Bình sang Chi”, append-only dòng mới | Chỉ An + host thấy thay đổi |
| Bỏ qua/rút | Dòng công khai tương ứng, tally cập nhật | Không count/badge/event cho người khác |
| Retry cùng ID và payload | Trả kết quả cũ, không dòng thứ hai | Như public |
| Cùng ID nhưng payload/actor khác | 409, không đọc lộ response người khác | Như public |
| Close hoặc hết giờ đua submit | Lock cùng room; kiểm tra now server sau lock, chỉ giao dịch hợp lệ commit | Như public |
| Mở vòng mới | Không đổi phiếu/mode của vòng cũ; có lịch sử chọn vòng | Như public |
| Sau trận | Toàn bộ phiếu/sửa/rút/quyết định được xem | Toàn bộ phiếu/sửa/rút/quyết định được xem theo consent |

Vote channel chứa vòng đang chọn và diễn biến của vòng đó, có selector lịch sử. Header mode luôn có chữ, không chỉ màu. Host tổng hợp phiếu/current tally trong panel riêng; secret không trả tally/submitted_count cho player. Tóm tắt công bố kết quả nằm ở Thông báo nếu host bấm đăng; đó là sự kiện khác với công khai phiếu và không làm public toàn bộ review.

## 6. Role

Host trước trận có sidebar Bộ role / Phát role; player Role của tôi / Danh mục. Khi chơi host chỉ Danh mục, phân role quản lý ở drawer. Danh mục có search/lọc phe/kind, số bản inline trước snapshot, icon và “Tiên tri soi ra”. Click role mở chi tiết; host có nút Sửa mở modal, textarea chiều cao cố định 180px, không resize theo active/passive. Modal có body cuộn + footer Lưu/Hủy cố định, xác nhận bỏ bản nháp nếu đã sửa. Icon upload hiện preview và giới hạn hiện có; không tải icon thay tên người chơi kín.

Phát role: hai lựa chọn cạnh nhau Ngẫu nhiên / Chọn tối đa 3, tổng ghế và suất trước khi bắt đầu. Draft dùng pool và số bản cấu hình hiện tại; role đã nhận giảm/rời pool; chỉ 1–2 lựa chọn thì hiển thị đúng số đó. Random có thể rút từ pool còn hợp lệ ngoài lời mời; host Random hộ đúng lượt. Trong prototype mock 3 lời mời, đổi trạng thái dữ liệu thực thuộc UX5. Role của tôi hiện description, seer/faction, quyền dùng và nút yêu cầu khi hợp lệ. Yêu cầu mở form text ngắn, không thêm loại kỹ năng hoặc số mục tiêu.

## 7. Quản lý

Sidebar theo trạng thái phòng, không xếp form nối đuôi bảng người chơi. Click người ở Chat hoặc Quản lý mở cùng PlayerDrawer; thông tin host có role thật, hiệu ứng và ghi chú, còn drawer player chỉ thông tin công khai. Các nút Giết/Hồi sinh, hiệu ứng, ghi chú, đổi role/phe/đội và nhắn riêng hiện gần đối tượng. Tin riêng đưa tới DM đúng người. Không hiện textarea lý do cho mọi thao tác.

Queue request lọc pending/in_review/đã xử lý; mở request có nội dung và role lúc gửi, áp dụng tác động, liên kết và phản hồi trong cùng drawer, không cần đổi tab rồi tìm lại người. Accept request không tự áp dụng kỹ năng; phản hồi và trạng thái ghi vào chính item trong DM.

Điều hành tập trung bắt đầu/chuyển pha/pause/end. Confirm có người/pha và phạm vi công bố; vote đang mở chặn chuyển pha, có nút mở kênh Vote. Lý do pause/end/quyết định vote/kick giữ yêu cầu hiện tại; ghi chú, DM, hiệu ứng thường không thêm trường bắt buộc. Cài đặt chat/slow mode/modal phòng có nút lưu rõ và version chống mất chỉnh sửa. Sau trận Quản lý chứa nhật ký, export và ván mới; lịch sử ván cũ giữ nguyên.

## 8. Tokens và trạng thái UI

| Token | Giá trị mẫu |
| --- | --- |
| Workspace / sidebar / input | #313338 / #2b2d31 / #383a40 |
| Text / secondary / separator | #f2f3f5 / #b5bac1 / #41434a |
| Primary / focus | #5865f2 / #9aa5ff (outline 2px + offset) |
| Font | system-ui; thân 15px/1.45, metadata 12px/1.4 |
| Avatar / message padding | 32px; 4px dọc, 16px ngang |
| Gap tin cùng nhóm / nhóm mới | 2–4px / 12–16px |
| Phe | Sói: đỏ; Làng: xanh; Trung lập: tím; luôn có chữ |
| Art | Icon role pixel 32/48px, dự phòng chữ/glyph; không nền lớn trong Chat |
| Reduced motion | Không animation cuộn bắt buộc, tắt transition trang trí |

Loading dùng skeleton hàng tin, không giả tin thực. Rỗng có một dòng hướng dẫn; composer chỉ đọc nêu lý do. Mất mạng có banner gọn không che lịch sử, gửi thất bại giữ nháp; 403/kick đóng kênh và xóa cache; hết phiên mở lại luồng đăng nhập. Sau trận có nhắc mọi kênh chỉ đọc. Modal dài có scroll riêng, không lồng nhiều modal.

## 9. Đánh giá UX1

Prototype là dữ liệu giả, không kết nối API, không chứng minh phân quyền, transaction hoặc tải 500 người. Chuyển vai/stage ở thanh “Bản nháp” chỉ phục vụ review. Các mẫu có thể bấm tab, đổi kênh, soạn/emoji, public/secret vote, role editor, draft, drawer người chơi/queue/cài đặt và trạng thái sau trận. [Biên bản kiểm tra](verification.md) ghi kích cỡ và giới hạn thực tế. E/F/G là nghiệm thu ứng dụng thật ở bước sau; không tick vì prototype chạy được.
