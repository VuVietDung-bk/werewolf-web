# Quy tắc vote mặc định của MVP

Chủ dự án đã chốt quy tắc này khi triển khai bước 5.7a. Vote là công cụ thu phiếu; quản trò quyết định kết quả và mọi thay đổi trạng thái người chơi bằng thao tác riêng. Hệ thống không ép đủ người bỏ phiếu.

| Tình huống | Quy tắc |
| --- | --- |
| Ngày 0 | Quản trò có thể mở vote ngay từ Ngày 0; không tự mở. |
| Mở/đóng vòng | Chỉ quản trò; chỉ mở khi trận đang chơi, đang là ban ngày và không tạm dừng. Có thể đóng vòng khi tạm dừng hoặc khi thiếu phiếu. Mỗi phòng chỉ có một vòng đang mở; có thể mở vòng khác sau khi đóng. |
| Người gửi phiếu | Thành viên có ghế và **được công bố là sống**; quản trò không có phiếu. Trạng thái chết kín không tự làm mất quyền vote để API không tiết lộ thông tin chưa công bố. |
| Người được chọn | Ghế đang được công bố là sống, kể cả chính mình. Quản trò xử lý ngoại lệ của role thủ công. |
| Nội dung phiếu | Chọn một người, bấm Bỏ qua, hoặc không gửi gì. Người đã gửi có thể đổi lựa chọn hoặc rút trước khi vòng đóng. Bỏ qua là một trạng thái đã gửi; rút phiếu trở về trạng thái không tính phiếu. |
| Khi tạm dừng/ban đêm/vòng đã đóng | Không nhận phiếu mới. Phải đóng vòng đang mở trước khi chuyển pha hoặc kết thúc trận. Màn điều khiển báo vòng còn mở và dẫn sang tab Vote; sau khi đóng, quản trò vẫn phải xác nhận lệnh chuyển pha/kết thúc. Server kiểm tra lại trạng thái để chặn lệnh nếu có thay đổi đồng thời. |
| Quyền xem trong trận | Người chơi thấy vòng đang mở/đóng và phiếu hiện tại của chính mình; không thấy phiếu hoặc số phiếu của người khác. Quản trò thấy từng phiếu, tổng hợp theo người được chọn, số bỏ qua, đã rút và chưa gửi. |
| Quyết định | Sau khi đóng vòng, quản trò ghi kết quả và lý do; có thể gắn một người liên quan hoặc để trống. Quyết định có thể khác số đông, hòa hoặc không loại ai. Lưu quyết định không tự công bố, giết, hồi sinh hoặc chuyển pha. |
| Công bố | Sau khi có quyết định, quản trò có thể chọn công bố kết quả, tên người đã gửi phiếu (không kèm lựa chọn), lý do, hoặc kết hợp các phần này. Ít nhất một phần phải được chọn. Không bấm công bố thì toàn bộ vẫn kín trong trận. Mỗi vòng chỉ công bố một lần; thông báo đã đăng có thể sửa/xóa trên tab Thông báo, bản gốc vẫn nằm trong sự kiện. |
| Sau trận | Sự kiện mở/đóng, từng phiếu, mọi lần đổi/rút và quyết định được giữ để đưa vào transcript ở bước 8. |

API hiện có: `GET /api/rooms/{id}/votes/latest`, `POST /api/rooms/{id}/votes`, `POST /api/rooms/{id}/votes/{vote_id}/ballots`, `POST /api/rooms/{id}/votes/{vote_id}/close`, `GET /api/rooms/{id}/votes/{vote_id}/review` (chỉ quản trò), `POST /api/rooms/{id}/votes/{vote_id}/decision` (chỉ quản trò), `POST /api/rooms/{id}/votes/{vote_id}/publication` (chỉ quản trò), `GET /api/rooms/{id}/votes/publications` (thành viên). Mỗi POST có `client_request_id`; lệnh lặp đúng mã không tạo thêm tác động. Vòng vote và phiếu được lưu bởi migration `0014_vote_rounds`, quyết định bởi `0015_vote_decisions`, lần công bố bởi `0016_vote_publications`. Lần gửi/đổi/rút phiếu, quyết định và công bố đều có sự kiện/outbox trong cùng giao dịch. Sự kiện công khai chỉ chứa dòng chữ đã được quản trò chọn; phiếu và quyết định gốc chỉ ở sự kiện kín cho transcript sau trận.
