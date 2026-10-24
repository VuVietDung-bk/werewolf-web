# Bước 9.4 · Rà soát bảo mật ứng dụng

Ngày rà soát: 2026-10-24. Phạm vi: API FastAPI, WebSocket, frontend React và cấu hình chạy local/staging trong repo. Đây là rà soát mã và kiểm thử hồi quy; cấu hình reverse proxy, TLS và giới hạn ở biên mạng phải được xác nhận tại bước 10.4.

| Hạng mục | Kết quả trong ứng dụng | Kiểm chứng |
| --- | --- | --- |
| Phiên/cookie | Token ngẫu nhiên 32 byte, DB chỉ lưu SHA-256; phiên có hạn 30 ngày, thu hồi khi logout; cookie `HttpOnly`, `SameSite=Lax`, `Secure` trừ `APP_ENV=local`. Quyền phòng đọc từ DB mỗi yêu cầu. | `test_foundation.py`: tạo phiên, hết hạn, thu hồi, phân quyền phòng và WebSocket. |
| CSRF | Mọi lệnh HTTP thay đổi dữ liệu yêu cầu `Origin` khớp chính xác `APP_ORIGINS`; thiếu, `null`, khác scheme hay origin giả dạng bị từ chối. GET chỉ đọc dữ liệu. | `test_security_review.py` và `test_foundation.py`. |
| WebSocket | Cả socket phiên và socket phòng kiểm tra Origin + phiên trước khi accept; socket phòng kiểm tra quyền phòng, đọc lại hiệu lực phiên/thành viên khi phát sự kiện, chỉ nhận dữ liệu từ server. | `test_foundation.py`, `test_live.py`; kiểm thử mất quyền và nối lại. |
| XSS và ảnh upload | Nội dung chat/role/thông báo hiển thị như text qua React; không có `dangerouslySetInnerHTML`, `innerHTML`, `document.write` hoặc `eval` trong `frontend/src`. Icon chỉ nhận PNG/JPEG/WebP tĩnh tối đa 1 MiB, cạnh tối đa 512 px, được giải mã và tạo PNG mới trước khi phục vụ với `nosniff`. | `test_chat_moderation.py` giữ nguyên payload HTML như text; `test_icons.py` kiểm tra định dạng và quyền. Frontend build đạt. |
| Tần suất | Chat: 6/10 giây và 30/60 giây; yêu cầu role: 3/30 giây và 10/300 giây, trả 429/`Retry-After`. Cả hai khóa hàng phòng trước khi kiểm tra và ghi, tránh vượt giới hạn khi gửi đồng thời. Giới hạn độ dài nội dung tại schema. | `test_chat_moderation.py`, `test_role_requests.py`; rà soát `with_for_update` ở hai đường ghi. |
| Log và cache | Uvicorn access log HTTP mặc định bị tắt vì chứa raw URL; log thay thế chỉ ghi method, mẫu route và status. URL WebSocket của Uvicorn được che. SQLAlchemy ẩn tham số truy vấn khi báo lỗi. Phản hồi API mặc định `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`; icon đã có cache riêng tư. | `test_security_review.py`: từ khóa và cookie giả không xuất hiện trong log, cả URL lạ; kiểm tra headers. |

Các thay đổi này theo hướng dẫn [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html) về Origin khớp chính xác và từ chối khi thiếu bằng chứng nguồn, [OWASP WebSocket Security](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html) về kiểm tra Origin/phiên, và [OWASP Logging](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html) về tránh ghi dữ liệu nhạy cảm.

## Việc phải xác nhận khi triển khai 10.4

- Reverse proxy/CDN phải dùng HTTPS/WSS, chỉ chuyển `Origin` hợp lệ, không ghi raw query, Cookie, Authorization, body chat hoặc WebSocket URL vào log. Bật giới hạn tần suất phân tán cho tạo phiên khách, tạo/tham gia phòng và kết nối WebSocket; các đường này chưa có giới hạn theo IP ở ứng dụng. Xác định IP thật từ proxy tin cậy, không tin `X-Forwarded-For` do client tự gửi.
- Chỉ đặt origin thật trong `APP_ORIGINS`, không dùng wildcard. Không bật `APP_ENV=local` hay `ENABLE_DEMO_MODE=1` trên staging/production. Giới hạn kích thước request và kết nối WebSocket ở proxy. Kiểm tra lại cookie `Secure` trên HTTPS và xem log proxy bằng một từ khóa thử không nhạy cảm.
- Cấu hình header bảo vệ cho HTML frontend tại máy phục vụ static, gồm CSP phù hợp với bundle thực tế. Header API không tự bảo vệ HTML do máy khác phục vụ. Kiểm tra lại bằng trình duyệt trong pilot.

## Kết quả chạy

- PostgreSQL/Redis local qua Docker Compose; `DATABASE_URL=... .venv/bin/pytest -q --tb=short backend/tests`: **36 passed** sau thay đổi cuối.
- `npm run build` ở `frontend/`: đạt. `git diff --check`: đạt.
