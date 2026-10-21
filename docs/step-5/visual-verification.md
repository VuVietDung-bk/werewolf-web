# Kiểm tra trực quan 5.6 và 5.7

Ngày kiểm tra: 20/10/2026. Dùng Chromium headless với API và frontend chạy tại máy, tạo phòng tạm gồm một quản trò và bốn người chơi. Phòng và các phiên tạm đã được xóa sau lượt thử.

## 5.6 · Art ngày/đêm và biểu tượng

| Tình huống | Desktop 1280 px | Điện thoại 390 px |
| --- | --- | --- |
| Ngày 0, ảnh nền tải được | [Quản trò](screenshots/day-host-desktop.png) | [Người chơi](screenshots/day-player-mobile-390.png) |
| Đêm 1, ảnh nền tải được | [Quản trò](screenshots/night-host-desktop.png) | [Người chơi](screenshots/night-player-mobile-390.png) |
| Ảnh nền bị chặn | [Ngày](screenshots/day-host-no-art-desktop.png) | [Ngày](screenshots/day-player-no-art-mobile-390.png), [đêm](screenshots/night-player-no-art-mobile-390.png) |
| Hiệu ứng, chết, hồi sinh | [Hiệu ứng và chết trên bảng quản trò](screenshots/day-board-death-desktop.png) | [Chết trên ghế](screenshots/day-death-glyph-mobile-390.png), [hồi sinh trong Thông báo](screenshots/night-revival-glyph-mobile-390.png) |

Nền đổi đúng theo pha. Chữ ở tiêu đề, ghế và bảng quản trò vẫn rõ khi tải ảnh và khi chủ động chặn cả hai URL ảnh nền. Biểu tượng luôn đi cùng nhãn chữ. Không có tràn ngang ở các màn đã chụp.

## 5.7 · Vòng vote

| Trạng thái | Minh chứng |
| --- | --- |
| Vòng mở; quản trò thấy tổng hợp 1–1, một phiếu bỏ qua, một người chưa gửi | [Desktop](screenshots/vote-open-host-desktop.png), [danh sách phiếu 390 px](screenshots/vote-open-host-ballots-mobile-390.png) |
| Người chơi thấy phiếu của mình và nút đổi/bỏ qua/rút, không thấy bảng phiếu người khác | [390 px](screenshots/vote-open-player-mobile-390.png) |
| Đóng khi thiếu phiếu, quản trò chốt “Không loại ai” kèm lý do dù hòa | [Quản trò 390 px](screenshots/vote-decided-host-mobile-390.png), [người chơi chỉ thấy vòng đã đóng](screenshots/vote-closed-player-mobile-390.png) |
| Quản trò chọn công bố kết quả; người chơi thấy trong tab Thông báo | [390 px](screenshots/vote-notice-player-mobile-390.png) |
| Vòng cũ vẫn xem lại được sau khi mở vòng mới | [Bộ chọn vòng của quản trò](screenshots/vote-history-host-mobile-390.png) |
| Màn hẹp | [Người chơi 320 px](screenshots/vote-closed-player-narrow-320.png) |

Luồng thử dùng giao diện để mở, đóng, chốt và công bố; một người bấm **Bỏ qua** trên giao diện. Hai phiếu chọn người được gửi qua API từ hai phiên người chơi riêng, người thứ tư không gửi. Chromium xác nhận màn người chơi không có bảng `.vote-review`, không có lý do kín trước công bố; API cũng trả 403 khi người chơi hoặc người ngoài xin danh sách các vòng và bảng phiếu. Quyết định vote không tự đổi trạng thái sống/chết. Sau khi công bố và chuyển ngày, quản trò mở vòng thứ hai, chọn lại vòng đầu và xem được quyết định cùng bảng phiếu.

Các màn vote đã được bổ sung trong [wireframe](../step-1/wireframes.html). Thanh điều hướng điện thoại nằm trên một hàng, cuộn ngang và tự đưa tab đang mở vào vùng nhìn thấy. Ở 1280, 390 và 320 px, phép đo `document.documentElement.scrollWidth` không vượt quá chiều rộng cửa sổ.
