# Bước 8 · Nhật ký sau trận

## Nhật ký và quyền xem

- `game_events` có ID, số thứ tự tăng trong phòng, actor, UTC, loại, phạm vi, payload và mã yêu cầu khi có. API `/api/rooms/{id}/transcript` dựng pha/ngày/đêm, người liên quan, lý do, bản trước/sau và liên kết sự kiện từ dữ liệu gốc. Không ghi đè tin đã sửa/xóa; mọi phiên bản và quyết định vẫn có dòng riêng.
- Trước khi kết thúc trận, API nhật ký và hai bản xuất trả `409`; người ngoài phòng trả `403`. Khi kết thúc, quản trò, người chơi hiện tại và người từng vào phòng đã đồng ý chính sách công bố đều đọc được. Phòng đã kết thúc xuất hiện lại ở “Phòng của tôi” cho người từng rời phòng.
- Trang “Nhật ký sau trận” chỉ có trên navigation khi trận đã kết thúc. Timeline gồm phát/chọn role (thứ tự, lời mời, lựa chọn tự chọn/ngẫu nhiên/chọn hộ), chat chung/đội/riêng, yêu cầu role, vote và từng phiếu, hành động/ghi chú, thông báo, quyết định giữ/bỏ qua, sửa/xóa và đính chính. Có lọc nhóm, tìm theo nội dung/actor/mã sự kiện, phân trang và đường sang sự kiện được liên kết. Mỗi thẻ có dữ liệu gốc để đối chiếu các trường chưa có phần tóm tắt riêng.
- JSON và CSV lấy cùng các dòng, sắp theo số thứ tự, tên trường ổn định và chứa dữ liệu gốc. CSV chặn ô văn bản bị hiểu là công thức. Xuất file và trang đọc gửi `Cache-Control: private, no-store`. Việc đính chính sau trận chỉ thêm sự kiện có lý do; sự kiện gốc vẫn giữ nguyên.
- Trang giải thích giới hạn: nhật ký là dữ liệu và thao tác đã diễn ra trên web; nó không tự chứng minh quản trò áp dụng luật role đúng hay ghi nhận việc ngoài web.

## Art và kiểm tra

Timeline dùng nền làng pixel cho trang sau trận, viền màu kèm nhãn chữ cho mốc Ngày/Đêm, và pixel glyph theo loại sự kiện. Icon không mã hóa phe hay role của người chơi. Bản xuất JSON/CSV và tóm tắt chữ không phụ thuộc art.

- `backend/tests/test_transcript.py` chạy ván mẫu có chọn role, yêu cầu/hiệu ứng, chat riêng đã xóa, ghi chú đã sửa, phiếu vote và quyết định quản trò, thông báo đã sửa, chết bị giữ rồi bỏ công bố, chat đội, kết thúc và đính chính sau trận. Test kiểm tra quyền trước/sau, người đã rời phòng, thứ tự/phân trang, lọc/tìm, metadata, xuất hai lần giống nhau và sự kiện gốc không đổi.
- Toàn bộ backend: **29 passed**. Frontend `npm run build`, Alembic `check`, `git diff --check` đạt. Một lần chạy toàn bộ ban đầu có timeout ở test WebSocket nối lại; test riêng qua và lần chạy toàn bộ tiếp theo 29/29 qua.
- Ảnh Chromium dùng dữ liệu timeline đại diện qua API giả lập để kiểm tra bố cục: [desktop](screenshots/transcript-desktop.png), [điện thoại](screenshots/transcript-mobile.png). Cả hai không tràn ngang; kiểm tra quyền và dữ liệu thực được xác minh riêng bằng test tích hợp PostgreSQL ở trên.

**Giới hạn vận hành:** bản xuất hiện tải toàn bộ sự kiện của một phòng vào bộ nhớ API. Bước 9 cần đo tải với phòng dài và cân nhắc xuất theo luồng nếu kích thước tăng đáng kể.
