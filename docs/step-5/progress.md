# Bước 5 · bàn điều khiển quản trò

## 5.1 · Điều khiển pha và kết thúc trận

- Quản trò chuyển pha thủ công theo Ngày 0 → Đêm 1 → Ngày 1 → Đêm 2. Mỗi lệnh gửi pha và số vòng đang thấy; màn hình cũ bị từ chối để tránh hai lệnh đồng thời làm nhảy hai pha.
- Quản trò tạm dừng với lý do, tiếp tục mà không đổi pha, và kết thúc bằng kết quả cùng giải thích. Kết thúc có bước xác nhận trên giao diện. Người chơi thấy trạng thái tạm dừng và kết quả sau khi kết thúc.
- Các lệnh chỉ dành cho quản trò. API khóa hàng phòng, ghi `game_events` và `outbox` trong cùng giao dịch với thay đổi trạng thái. Khi tạm dừng, không chuyển pha; ràng buộc quyền gửi chat/yêu cầu trong trạng thái này sẽ được thực hiện ở bước 6–7.
- Migration `0008_game_flow` thêm cờ tạm dừng, kết quả và thời điểm kết thúc. Màn trận vẫn có trong thanh điều hướng sau khi kết thúc.

**Kiểm tra:** `alembic check` không báo chênh lệch, `pytest -q backend/tests` đạt **12 passed**, `npm run build` đạt. Test tích hợp xác nhận quyền host, lý do rỗng, tạm dừng/tiếp tục, hai lệnh chuyển cùng pha, chu kỳ Ngày 0 → Đêm 1 → Ngày 1, kết quả thủ công, chặn thao tác sau kết thúc và đủ cặp sự kiện/outbox. Chromium xác nhận quản trò thao tác qua màn trận, người chơi thấy tạm dừng và kết quả sau tải lại ở màn 390 px, không tràn ngang. Dữ liệu kiểm tra đã được dọn khỏi DB local.

## 5.2a · Trạng thái kín và thao tác đầu tiên trên người chơi

- Tab **Bàn quản trò** chỉ xuất hiện cho chủ phòng khi trận đang chơi hoặc đã kết thúc. Bảng hiển thị role, phe, trạng thái sống/chết, hiệu ứng và số ghi chú của từng người; có lọc sống/chết. Chọn một người mở hộp thao tác, giữ màn trận gọn.
- Quản trò có thể giết, hồi sinh, thêm/gỡ hiệu ứng và ghi chú. Giết/hồi sinh cần xác nhận; mọi thao tác trừ ghi chú cần lý do. Trạng thái kín nằm trong `player_states`; ghi chú nằm trong `player_notes`. Cả hai bảng được thêm ở migration `0009_player_state`; người chơi trong trận cũ được đưa vào bảng khi nâng migration.
- Endpoint bảng/ghi chú chỉ trả dữ liệu cho quản trò. `GET /api/rooms/{id}` và danh sách ghế không chứa trạng thái chết hay hiệu ứng chưa công bố. Các thao tác ghi `game_events`/`outbox` trong giao dịch thay đổi trạng thái. `client_request_id` chặn gửi lặp cùng thao tác; giết hai lần, hồi sinh người đang sống và hiệu ứng trùng bị từ chối. Khi tạm dừng, quản trò vẫn ghi chú được nhưng không đổi trạng thái.
- Kiểm tra tích hợp trên PostgreSQL xác nhận quyền host, dữ liệu kín, gửi lặp, trạng thái sau giết/hồi sinh/thêm/gỡ hiệu ứng, ghi chú và cặp sự kiện/outbox. Chromium ở 390 px xác nhận tab kín, hộp thao tác, dữ liệu giữ sau tải lại và không tràn ngang. Phòng/tài khoản thử đã được xóa khỏi DB local.

## 5.2b · Công bố chết và hồi sinh

- Trạng thái thật `is_alive` và trạng thái đã công bố `public_is_alive` được lưu riêng. Giết hoặc hồi sinh chỉ đổi trạng thái thật; quản trò bấm **Công bố cho toàn phòng** để đổi trạng thái công khai. Có ô nguyên nhân công khai và tùy chọn tiết lộ tên role. Vai trò đã tiết lộ tiếp tục hiện trong danh sách công khai.
- Tab **Người chết** cho tất cả thành viên hiển thị danh sách người đang được công bố là đã chết cùng lịch sử thông báo chết/hồi sinh. Danh sách ghế ở tab Trận đấu dùng cùng trạng thái công khai. Trước công bố, API phòng và tab này giữ nguyên trạng thái cũ; chỉ quản trò thấy trạng thái thật. Thông báo công khai không mang lý do riêng của quản trò.
- Một lần công bố tạo sự kiện quyết định kín và sự kiện thông báo công khai trong cùng giao dịch. `client_request_id` chặn gửi lặp; không có thay đổi đang chờ thì API từ chối. Có thể công bố khi trận tạm dừng, phù hợp quyền thông báo của quản trò.
- Migration `0010_public_announcements` thêm trạng thái công khai và `public_version`. Số phiên bản trong API phòng của người chơi chỉ tăng vì sự kiện công khai, nên thao tác kín không phát tín hiệu thay đổi qua cơ chế cập nhật phòng. Số phiên bản nội bộ của quản trò vẫn theo toàn bộ sự kiện.
- Kiểm tra: `pytest -q backend/tests` **12 passed**, `npm run build` và `alembic check` đạt. Test PostgreSQL kiểm tra quyền, trạng thái kín/công khai, công bố role tùy chọn, lý do riêng không xuất hiện trong thông báo, gửi lặp, sự kiện/outbox. Chromium ở 390 px đi hết luồng giết kín → công bố chết → hồi sinh kín → công bố hồi sinh; tab và danh sách khớp sau tải lại, không tràn ngang. Dữ liệu thử đã được xóa.

## 5.2c · Đổi role, phe, đội chat và gửi tin riêng

- Quản trò chọn người trong **Bàn quản trò**, mở tab **Role và phe** để đổi role từ bản lưu trận, đổi riêng phe hoặc đội chat; mọi thay đổi cần lý do và xác nhận. Đổi role lấy phe và đội chat mặc định của role mới. Có thể bỏ đội chat bằng tên trống. Bản phân role đầu tiên trong `role_assignments` và danh mục role của ván không bị sửa; `player_states` lưu thông tin hiện tại. Người sở hữu thấy role hiện tại ở tab **Role của tôi**; API phòng của người khác không tiết lộ thay đổi kín.
- Tab **Tin riêng** trong hộp thao tác cho phép quản trò gửi thông tin cho từng người. Người nhận xem trong tab riêng của mình, được cập nhật định kỳ. Tin lưu bền trong `private_messages`; nội dung nằm trong sự kiện kín của quản trò. API vẫn nhận lý do tùy chọn để tương thích với client cũ, nhưng giao diện không hỏi lý do. Tin gửi được khi tạm dừng; thao tác đổi role/phe/đội chat bị chặn khi tạm dừng.
- Migration `0011_role_private` thêm cột trạng thái hiện tại và bảng tin riêng. Cả thao tác đổi danh tính và gửi tin đều ghi sự kiện/outbox cùng giao dịch; `client_request_id` chống gửi lặp. Test tích hợp xác nhận snapshot gốc, người khác không đọc được tin/role, bản công khai không tăng phiên bản vì thay đổi kín, công bố đúng role hiện tại, lặp yêu cầu, tạm dừng và kết thúc.
- Kiểm tra: `pytest -q backend/tests` **13 passed**, `npm run build`, `alembic check`, `git diff --check` đều đạt. Chưa kiểm tra trực quan bằng trình duyệt trong lượt này vì môi trường hiện không có Chromium/Playwright.

## 5.3 · Đính chính và tính nguyên tử của thao tác

- API `GET /api/rooms/{id}/game/events/host` cho quản trò xem 50 sự kiện gần nhất với số thứ tự, dữ liệu và phạm vi hiển thị. Giao diện tab **Nhật ký quản trò** từng dùng API này đã được bỏ theo phản hồi sau đó; việc Sửa/Xóa nội dung nay nằm ngay trên từng tin/thông báo. API và sự kiện gốc vẫn phục vụ kiểm chứng sau trận.
- `POST /api/rooms/{id}/game/events/corrections` không sửa sự kiện gốc. Một sự kiện kín tạo đính chính kín; một sự kiện đã công bố tạo quyết định kín và một đính chính công khai. Lý do riêng chỉ ở sự kiện kín. Người chơi thấy đính chính công khai trong tab **Thông báo**; bản gốc giữ nguyên để đối chiếu. Nếu trạng thái người chơi sai, quản trò dùng hành động sửa trạng thái riêng; việc đính chính chữ không tự thay đổi trạng thái.
- Mỗi sự kiện mới có hàng `outbox` trong cùng giao dịch. `client_request_id` trả kết quả cũ khi gửi lại cùng nội dung và từ chối tái dùng cho nội dung khác. API chặn người chơi thường và người ngoài khỏi danh sách/quyết định kín.
- Test tích hợp cố ý gây lỗi giữa thay đổi trạng thái và ghi sự kiện: request trả 500, trạng thái, phiên bản phòng, số sự kiện và outbox giữ nguyên. Test thêm đính chính kín/công khai, bản gốc không đổi, lý do kín không xuất hiện trong API công khai, gửi lặp và quyền truy cập.

**Kiểm tra:** `pytest -q backend/tests` **15 passed**, `npm run build`, `git diff --check` và Python compile đạt. Chưa kiểm tra trực quan trên trình duyệt ở lượt này. Giao diện hiện tách danh sách đính chính công khai khỏi lịch sử thông báo chết/hồi sinh; gộp timeline ở bước 7–8.

## 5.4 · Trạng thái hợp lệ và gửi lặp

- Các lệnh trên người chơi, công bố chết/hồi sinh và tin riêng kiểm tra `client_request_id` sau khi khóa phòng nhưng trước khi từ chối trận đã kết thúc. Gửi lại đúng lệnh đã ghi trả `repeated: true`; lệnh mới vẫn nhận 409. Cùng một mã với nội dung hoặc loại lệnh khác nhận 409.
- Bắt đầu, chuyển pha, tạm dừng, tiếp tục và kết thúc hỗ trợ `client_request_id` trong body. Client web gửi mã này cho mọi nút điều khiển. Gửi lại lệnh cũ sau một chu kỳ tạm dừng/tiếp tục không tạo thêm pha hoặc đổi lại trạng thái; request thiếu mã vẫn được nhận để tương thích với client cũ và tiếp tục dựa vào kiểm tra trạng thái/pha hiện tại.
- Giao diện giữ cùng mã cho lần thử lại của cùng thao tác khi chưa nhận xác nhận API, kể cả khi đổi tab trong cùng phiên trang. Khi API xác nhận nhưng bước tải lại thất bại, các form có thể gây gửi trùng được xóa nội dung hoặc giữ mã đã ghi, và báo rõ lệnh đã thực hiện.
- Test tích hợp kiểm tra hai lệnh giết cùng mã đến đồng thời tạo đúng một sự kiện, giết hai lần bằng mã khác và hồi sinh người đang sống bị chặn, thêm hiệu ứng trùng bị chặn, gửi lại hành động/tin riêng/công bố sau kết thúc chỉ trả kết quả cũ. Test chuyển pha lặp, mã tạm dừng cũ sau khi tiếp tục, mã tiếp tục cũ khi đang tạm dừng lần nữa và mã bị dùng cho lệnh khác.

**Kiểm tra:** `pytest -q backend/tests` **15 passed**, `npm run build`, Python compile và `git diff --check` đạt. Chưa kiểm tra trực quan trên trình duyệt ở lượt này.

## Điều chỉnh UX · Sửa/Xóa tại từng nội dung

- Bỏ tab **Nhật ký quản trò** khỏi điều hướng. Bên cạnh tin riêng đã gửi và từng thẻ trong tab **Thông báo**, quản trò thấy **Sửa** và **Xóa** khi trận đang chơi. Người chơi chỉ thấy nội dung hiện tại cùng nhãn “Đã sửa”; nội dung đã xóa không còn trong danh sách. Với thông báo chết/hồi sinh, quản trò sửa được nguyên nhân công khai. Xóa thẻ thông báo không đảo trạng thái sống/chết đã công bố ở danh sách người chơi.
- Migration `0012_content_revisions` lưu bản đang hiển thị và mốc sửa/xóa. API `POST /api/rooms/{id}/game/content/{event_id}/revisions` chỉ cho quản trò sửa/xóa tin riêng hoặc thông báo đã công bố. Mỗi lần thay đổi thêm sự kiện và outbox trong cùng giao dịch; nội dung gốc vẫn nằm trong sự kiện đầu tiên. Sửa/xóa thông báo tạo thêm sự kiện công khai; lý do và bản cũ không rò qua API người chơi. Có thể gửi lại cùng `client_request_id`; lệnh mới sau trận bị từ chối.
- Test tích hợp xác nhận người khác không sửa/xóa, tin riêng sau sửa chỉ người nhận thấy, tin/thông báo xóa biến mất khỏi API trong trận, bản gốc vẫn nằm trong DB, phiên bản công khai chỉ tăng khi sửa/xóa thông báo, gửi lặp không nhân đôi và lỗi ghi sự kiện rollback bản đang hiển thị.

**Kiểm tra:** `pytest -q backend/tests` **16 passed**, `alembic check`, `npm run build` và `git diff --check` đạt. Chưa có kiểm tra trực quan bằng trình duyệt trong lượt này. Khi chat chung/đội và thông báo tùy chỉnh được triển khai ở bước 6–7, áp dụng cùng nút Sửa/Xóa và cùng chính sách lưu lịch sử.

## Điều chỉnh UX · Ghi chú và tin riêng

- Mỗi ghi chú trong hộp thoại người chơi có nút **Sửa/Xóa**. Bản sửa hiện nhãn “Đã sửa”; bản xóa biến mất và số ghi chú trên bảng giảm theo. Chỉ quản trò thao tác được khi trận đang chơi, kể cả lúc tạm dừng. Bản gốc vẫn ở `player_notes.text` và sự kiện tạo ghi chú; sự kiện sửa/xóa mới ghi lại từng thay đổi.
- Migration `0013_note_revisions` bổ sung bản ghi chú đang hiển thị và mốc sửa/xóa. API `POST /api/rooms/{id}/game/content/notes/{note_id}/revisions` hỗ trợ gửi lại cùng `client_request_id`; thay đổi ghi chú và sự kiện/outbox cùng giao dịch. Form tin riêng chỉ cần nội dung; API nhận `reason` tùy chọn cho client cũ. Tin mới từ giao diện không chứa trường lý do trong sự kiện.

**Kiểm tra:** `pytest -q backend/tests` **16 passed**, `alembic check`, `npm run build` và `git diff --check` đạt. Test xác nhận quyền quản trò, bản sửa/xóa, số ghi chú, gửi lặp và rollback khi ghi sự kiện lỗi. Chưa kiểm tra trực quan bằng trình duyệt trong lượt này.

## Tiếp theo

5.5a: bảng quản trò làm mới ngay sau thao tác, khi phiên bản phòng đổi và mỗi 8 giây lúc đang mở. Bộ lọc kết hợp trạng thái sống/chết, phe, hiệu ứng và người có trạng thái chờ công bố; số người đang hiện được ghi rõ. Bộ lọc phe/hiệu ứng lấy từ dữ liệu hiện tại và tự xóa khi giá trị không còn tồn tại. `npm run build` đạt; chưa có kiểm tra trực quan vì môi trường không có Chromium. 5.5b vẫn chờ dữ liệu từ luồng gửi/xử lý yêu cầu role ở bước 7; trạng thái **chờ công bố** hiện tại không được tính như yêu cầu role.

5.6a: tạo `village-day-v1.png` bằng built-in ImageGen từ nền làng ban đêm đã duyệt, giữ cùng bố cục và thêm ánh bình minh. Màn trận và bàn quản trò đổi nền theo pha; lớp phủ tối giữ chữ rõ, nền màu dự phòng có sẵn nếu ảnh lỗi. Biểu tượng pixel cho hiệu ứng, chết và hồi sinh là SVG lưới nhỏ, luôn xuất hiện cạnh nhãn chữ ở bảng quản trò, ghế người chơi hoặc tab Thông báo. Prompt gốc ghi trong `frontend/public/assets/README.md`; frontend build đạt. Kiểm tra trực quan 5.6b và ảnh chụp nằm trong [biên bản](visual-verification.md).

5.7a–b: chủ dự án đã giữ quy tắc mặc định ở [vote-rules.md](vote-rules.md): Ngày 0 được vote khi quản trò mở, phiếu có thể đổi/rút, bỏ qua hoặc không gửi; người chơi chỉ thấy phiếu mình. Migration `0014_vote_rounds` thêm vòng vote và phiếu hiện tại, còn từng lần gửi/đổi/rút nằm trong sự kiện kín và outbox cùng giao dịch. Tab **Vote** riêng cho quản trò mở/đóng vòng và người chơi gửi phiếu; vòng vẫn đóng được khi thiếu phiếu. Quyền gửi và chọn mục tiêu dựa trên trạng thái sống đã **công bố** để không lộ cái chết kín. Pha đêm/tạm dừng/vòng đóng chặn phiếu; vòng mở chặn chuyển pha hoặc kết thúc trận cho đến khi quản trò đóng. Chưa có chốt kết quả hoặc tự tác động người chơi.

Kiểm tra: `pytest -q backend/tests` **17 passed**, `alembic check` và `npm run build` đạt. Test vote xác nhận quyền host/người chơi/người ngoài, Ngày 0, phiếu riêng, bỏ qua/rút, gửi lặp, không đủ phiếu, sai pha, người chết công khai, cặp sự kiện/outbox. Chưa kiểm tra trực quan trên trình duyệt.

Phần đính chính thông báo chết/hồi sinh thành một dòng trong timeline chung vẫn nằm ở 7.4 và 8.3.

## 5.7c · Tổng hợp phiếu và quyết định thủ công

- Quản trò thấy từng ghế và phiếu hiện tại, tổng số theo người được chọn, số bỏ qua, đã rút và chưa gửi. API `GET /votes/{vote_id}/review` chỉ cho quản trò; người chơi tiếp tục chỉ đọc được phiếu của mình qua `GET /votes/latest`.
- Sau khi đóng vòng, quản trò nhập kết quả và lý do, có thể gắn người liên quan hoặc để trống. Hộp xác nhận hiện nội dung trước khi lưu. API `POST /votes/{vote_id}/decision` lưu đúng một quyết định trong `vote_rounds` cùng sự kiện `vote.decided` kín và outbox; `client_request_id` cho phép gửi lại mà không ghi trùng. Migration `0015_vote_decisions` bổ sung dữ liệu quyết định.
- Quyết định có thể khác số đông hoặc không loại ai khi thiếu phiếu. Nó không tự công bố, đổi pha hoặc đổi trạng thái sống/chết; quản trò dùng thao tác trên người chơi riêng. Công bố vote tùy chọn sẽ làm ở 5.7d.

Kiểm tra: `pytest -q backend/tests` **17 passed**, `alembic check`, `npm run build`, `git diff --check` đạt. Test tích hợp kiểm tra chặn người chơi xem bảng phiếu, quyết định khi vòng mở, chốt khác số đông, chốt không loại ai khi thiếu phiếu, gửi lại cùng mã, không tự giết và không lộ quyết định qua API công khai. Chưa kiểm tra trực quan trên trình duyệt.

## 5.7d · Công bố kết quả vote theo phần được chọn

- Sau khi chốt, quản trò chọn riêng **kết quả**, **tên người đã gửi phiếu** (không kèm lựa chọn) và **lý do**. Bỏ hết cả ba bị chặn; nếu muốn giữ kín thì không công bố. Form hiện trước chính xác nội dung sẽ công bố và hỏi xác nhận. Mỗi vòng chỉ công bố một lần.
- `POST /votes/{vote_id}/publication` ghi quyết định công bố kín và thông báo `vote.result_announced` công khai cùng outbox. Payload công khai chỉ có dòng chữ đã chọn, số ngày và ID vòng; không chứa phiếu, quyết định gốc hay phe. `GET /votes/publications` chỉ trả các thông báo đã công bố cho thành viên; người ngoài bị chặn. Migration `0016_vote_publications` lưu mốc đã công bố.
- Tab **Thông báo** hiện các kết quả vote đã đăng, có nút **Sửa/Xóa** như thông báo khác. Bản gốc và sửa/xóa vẫn nằm trong sự kiện; xóa khỏi tab không mở quyền công bố lại vòng đó. Quyết định vote và trạng thái người chơi không bị thay đổi bởi thao tác công bố.

Kiểm tra: test vote xác nhận trước công bố danh sách trống, công bố chỉ kết quả không lộ tên/lý do, công bố tên/lý do không lộ kết quả, quyền người ngoài/người chơi, gửi lặp, công bố lần hai bị chặn, sửa/xóa và sự kiện/outbox. WebSocket chưa triển khai; kiểm tra phát nội dung theo quyền được giữ cho 6.3. Chưa kiểm tra trực quan trên trình duyệt.

## 5.7e · Giao dịch vote và chuyển pha an toàn

- Giữ khóa hàng phòng cho các lệnh vote, quyết định và công bố. Hai lệnh quyết định cùng lúc trên một vòng cho đúng một thành công; lệnh còn lại nhận 409. Gửi lại chính lệnh đã ghi với cùng `client_request_id` trả `repeated: true`.
- Test cố ý gây lỗi sau khi thêm sự kiện/outbox cho lần lượt mở vote, gửi phiếu, đóng vòng, quyết định và công bố. Mỗi request lỗi trả 500; trạng thái vòng/phiếu, phiên bản phòng, sự kiện và outbox đều trở về trước request. Gửi lại đúng request sau khi hết lỗi thành công. Test kiểm tra thứ tự sáu sự kiện vote và đủ một outbox cho mỗi sự kiện.
- Màn **Điều khiển quản trò** hiển thị cảnh báo khi vòng vote còn mở, dẫn sang tab Vote; nút chuyển pha/kết thúc kiểm tra lại trạng thái ngay lúc bấm. Vòng mở không gửi lệnh chuyển pha/kết thúc. Sau khi đóng, xác nhận chuyển pha/kết thúc hiện như cũ. Server vẫn chặn vòng mở nếu có request khác thay đổi trạng thái sau lần kiểm tra ở client.

Kiểm tra: `pytest -q backend/tests` **18 passed** (riêng vote: 2 passed), `alembic check`, `npm run build` và `git diff --check` đạt. Test tích hợp cũ đã xác nhận API chuyển pha và kết thúc trả 409 khi vòng còn mở. Chưa kiểm tra thao tác cảnh báo bằng trình duyệt; sẽ kiểm tra trong B.9–B.10.

## 5.6b và 5.7f · Kiểm tra trực quan

- Dùng Chromium headless mở cùng một phòng bằng phiên quản trò và bốn phiên người chơi. Kiểm tra nền ngày/đêm, biểu tượng hiệu ứng/chết/hồi sinh và trường hợp ảnh nền bị chặn trên desktop 1280 px, điện thoại 390 px. Tab Vote còn được kiểm tra ở 320 px. Ảnh và kết quả nằm trong [biên bản trực quan](visual-verification.md).
- Bổ sung wireframe Vote cho người chơi, quản trò và trạng thái thông báo đã công bố. Thanh điều hướng trên điện thoại cuộn ngang trong một hàng; khi chuyển màn, tab đang chọn tự vào vùng nhìn thấy.
- Luồng thật: Ngày 0 mở vote; hai người chọn hai mục tiêu khác nhau, một người bỏ qua, một người không gửi. Quản trò thấy hòa 1–1, đóng vòng dù thiếu phiếu, chốt không loại ai kèm lý do rồi công bố phần kết quả. Người chơi không thấy bảng phiếu, lý do kín hay quyết định gốc. Sau khi mở vòng Ngày 1, quản trò chọn lại vòng Ngày 0 để xem bảng phiếu và quyết định. API chỉ cho quản trò liệt kê và xem lại các vòng vote.
- Test tích hợp thêm kiểm tra danh sách vòng chỉ dành cho quản trò, đúng thứ tự mới trước cũ, số người gửi và mốc chốt/công bố. Toàn bộ suite **18 passed**; `alembic check`, frontend build và `git diff --check` đạt. Phòng thử đã xóa.

## 5.8 · Hộp xác nhận và thông báo tùy chỉnh

- Thay các hộp xác nhận mặc định cho bắt đầu, chuyển pha, tạm dừng/tiếp tục, kết thúc, đánh dấu và công bố chết/hồi sinh, mở/đóng/chốt/công bố vote, cùng đăng/sửa/xóa thông báo công khai bằng một hộp thoại chung. Hộp nêu pha, đối tượng, phạm vi và phần người chơi thấy ngay; quyết định kín được ghi rõ. Nút Hủy nhận focus, Escape hủy, có thể mở lại ngay sau khi hủy.
- Tab **Thông báo** cho quản trò đăng nội dung tự do sau khi xem trước; người chơi chỉ thấy bản đã đăng. API `GET/POST /game/events/announcements` dùng sự kiện công khai và outbox, có idempotency theo `client_request_id`. Nút **Sửa/Xóa** dùng hệ thống `ContentRevision` sẵn có; bản gốc vẫn trong sự kiện. Chưa có loại sự kiện, người liên quan hoặc liên kết hành động/vote của bước 7.5.
- [Biên bản trình duyệt và ảnh chụp](confirmation-verification.md) kiểm tra Hủy/Xác nhận từng luồng, phím Escape, điện thoại 390 px và số sự kiện chính xác một lần. API test kiểm tra quyền, gửi lặp, sửa/xóa và rollback. Toàn bộ backend **19 passed**; frontend build, `alembic check` và `git diff --check` đạt.
