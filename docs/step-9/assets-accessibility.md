# Bước 9.6 · Asset, tải theo màn hình và khả năng truy cập

Ngày kiểm tra: 24/10/2026. Kiểm tra bản `npm run build` bằng Chromium chạy cục bộ; API được giả lập với một người chơi và một phòng Ngày 1 để ảnh chụp không chứa dữ liệu thật.

## Dung lượng và đường tải

| Mục | Trước | Sau | Kết quả |
| --- | ---: | ---: | --- |
| Ba ảnh nền/concept phục vụ qua web | 6.303.827 byte PNG | 1.040.398 byte WebP | Giảm 83,5%; PNG gốc ở `docs/art-source/v1/`, ngoài `dist` |
| Toàn bộ `frontend/dist` | Khoảng 6,6 MiB | 1,6 MiB | Bản phát hành nhỏ hơn khoảng 5 MiB |
| JS chính | Khoảng 373 KiB, gzip 105 KiB | 246,33 KiB, gzip 77,09 KiB | Các màn role/chat/vote/bàn quản trò/nhật ký tải bằng `React.lazy` khi mở |

Giữ nguyên kích thước và bố cục của ảnh; WebP chất lượng 90 đã được so trực quan với PNG gốc. Ở trang chủ, trình duyệt chỉ yêu cầu JS chính, CSS và nền đêm. Khi mở màn trận mới yêu cầu `GameControls` và nền ngày; khi mở danh mục role mới yêu cầu `RolePanel` và icon Tiên Tri. Ảnh concept chỉ được yêu cầu với `?asset-demo`. [Dữ liệu request và thời gian tải](browser-audit.json) ghi lại từng màn.

Mẫu [cấu hình cache Nginx](../../deploy/nginx-static-cache.conf.example) cho ảnh có tên phiên bản và JS/CSS có hash cache 1 năm `immutable`; `index.html` dùng `no-cache` để lấy manifest mới. Ảnh role tải lên thuộc API có chính sách cache riêng. Cấu hình proxy thật cần áp dụng và kiểm tra header ở bước 10.4.

## Kiểm tra giao diện

Ảnh chụp: [trang chủ desktop](screenshots/home-desktop.png), [trang chủ điện thoại](screenshots/home-mobile.png), [màn trận desktop](screenshots/game-desktop.png), [màn trận điện thoại](screenshots/game-mobile.png), [thiếu ảnh trên điện thoại](screenshots/missing-art-mobile.png). Kích thước viewport: 1280×900 và 390×844; ảnh chụp toàn trang.

| Cặp màu chữ/nền đặc | Tỷ lệ tương phản |
| --- | ---: |
| Chữ chính / nền trang | 14,85:1 |
| Chữ phụ / panel | 8,40:1 |
| Nhãn xanh / panel | 6,78:1 |
| Placeholder / ô nhập | 7,69:1 |
| Chữ / nút phụ | 10,04:1 |
| Chữ / nút chính | 9,77:1 |

Các cặp chữ nhỏ đại diện đều vượt [mức 4,5:1 của WCAG 2.2](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum). Ảnh nền có lớp phủ tối; đã tăng lớp phủ phía trên ảnh sảnh trên điện thoại để nhãn xanh dễ đọc. Kiểm tra trực quan cả ảnh bình thường và khi chặn mọi WebP: tiêu đề, nút, trạng thái và nội dung vẫn đọc được trên nền màu dự phòng.

Ở trang chủ, màn trận và danh mục mẫu, không có phần tử `img` thiếu thuộc tính `alt`. Icon role trang trí có `alt=""` cạnh tên role bằng chữ; ảnh mẫu cố tình bị chặn chuyển sang nhãn `role="img"`. Cách dùng `alt=""` cho ảnh trang trí theo [hướng dẫn WAI](https://www.w3.org/WAI/tutorials/images/decorative/). Biểu tượng trạng thái luôn đi kèm nhãn chữ.

Trong ngữ cảnh `prefers-reduced-motion: reduce`, Chromium xác nhận thời lượng animation/transition bị hạ còn 0,01 ms, số vòng lặp còn 1, delay 0 và cuộn mượt bị tắt. Không có lỗi JavaScript khi chuyển giữa trang chủ, trận và danh mục role.

## Kết nối chậm giả lập

Chromium tắt cache, đặt độ trễ 150 ms, tải xuống 200.000 byte/giây và tải lên 100.000 byte/giây. API cục bộ được giả lập, nên các số này phản ánh tải frontend và ảnh, không phản ánh độ trễ API thật hay thiết bị yếu.

| Mốc | Thời gian |
| --- | ---: |
| Mở trang chủ đến khi tiêu đề xuất hiện | 0,80 giây |
| Mở trang chủ đến khi tài nguyên của màn này tải xong | 3,20 giây |
| Chuyển sang màn trận đến khi tiêu đề xuất hiện | 0,84 giây |
| Chuyển sang màn trận đến khi nền ngày tải xong | 2,59 giây |

Kiểm tra staging với mạng và proxy thực tế thuộc kiểm tra D.5 và bước 10.4.
