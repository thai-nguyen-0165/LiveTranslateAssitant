# LiveTranslateAssitant

LiveTranslateAssitant là extension Chrome phiên dịch trực tiếp dành cho người Việt.
Extension lấy audio của tab hiện tại, Gemini Live tự nhận diện ngôn ngữ nguồn
và dịch liên tục sang tiếng Việt, sau đó thainp-tts local đọc bản dịch qua loa.
Audio gốc của tab vẫn được giữ để người dùng nghe song song.

## Kiến trúc

Google Meet / Zoom / YouTube / bất kỳ tab HTTPS nào
→ chrome.tabCapture → PCM 16 kHz
→ FastAPI backend
→ Gemini Live API (một phiên liên tục) → transcript tiếng Việt
→ thainp-tts v3 Turbo ONNX INT8
→ panel trên tab + loa máy người dùng

Gemini Live tự nhận diện ngôn ngữ nguồn; popup không còn yêu cầu chọn ngôn ngữ.
Đầu ra luôn cố định là tiếng Việt (`GEMINI_LIVE_TARGET_LANGUAGE=vi`). Audio do
Gemini tạo ra không được phát; chỉ transcript tiếng Việt được chuyển cho
thainp-tts local để giữ giọng đọc đã chọn trong extension.

Backend giữ một phiên Live Translate liên tục, không gọi một request dịch riêng
cho từng từ hoặc từng mảnh transcript. UI không render bản dịch partial trong
một vùng preview riêng để tránh lặp nội dung và nhấp nháy chiều cao panel; chỉ
những đoạn đã được đưa vào hàng đợi TTS mới xuất hiện trong transcript. Ngay
khi transcript tiếng Việt có dấu kết thúc câu, câu đó được đưa vào hàng đợi
thainp-tts lập tức. Nếu câu chưa có dấu câu nhưng đã đủ `TTS_MIN_WORDS` từ nội
dung (mặc định 10), LiveTranslateAssitant đọc ngay một chunk `TTS_CHUNK_WORDS` từ nội dung
(mặc định 10) thay vì chờ người nói
kết thúc cả lượt nói. Điều này giúp bản dịch và giọng đọc chạy song song, giảm
độ trễ. Panel có chiều cao cố định và phần transcript tự cuộn.

## Logic cấu hình hiện tại

- Provider duy nhất: Gemini Live.
- Bắt buộc: `GEMINI_API_KEY`.
- Gemini tự nhận diện ngôn ngữ nguồn; extension luôn gửi `source_lang=auto`.
- Ngôn ngữ đích cố định: tiếng Việt.
- Giọng đọc chạy local bằng thainp-tts v3 Turbo ONNX INT8.
- Popup cho phép chỉnh âm lượng TTS và âm thanh tab từ 0–200%, cùng tốc độ đọc từ 0.5x–2x;
  tốc độ được xử lý ở backend bằng tempo-preserving `ffmpeg atempo` để giữ
  cao độ giọng, còn âm lượng được áp dụng ở bộ phát audio.
- Các từ đệm như “à”, “ờm”, “ừm”, “hả” vẫn hiện trên transcript nhưng được bỏ
  qua ở TTS và không bị tính vào ngưỡng từ để không làm giọng đọc bị vụn; có
  thể bật/tắt bằng
  `TTS_SKIP_FILLERS`.

## Chạy backend

Yêu cầu: Python 3.11+, máy có RAM đủ cho TTS, `ffmpeg` trong PATH và Gemini API key.

    cd backend
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env

Điền `GEMINI_API_KEY` trong `backend/.env`, sau đó chạy:

    .venv/bin/python -m uvicorn server:app --host 127.0.0.1 --port 8000

`GEMINI_LIVE_MODEL` mặc định là
`gemini-3.5-live-translate-preview`.

## Deploy backend bằng Docker

Backend có thể chạy trên một VM Linux có Docker. Vì backend tải model
VieNeu-TTS ONNX local khi nhận request TTS, nên nên dùng VM có ít nhất 4 GB
RAM; các free web service 512 MB thường không đủ cho phiên dịch thực tế.

    cd backend
    docker build -t LiveTranslateAssitant-backend .
    docker run --name LiveTranslateAssitant-backend --env-file .env \
      -e PORT=8000 -p 8000:8000 LiveTranslateAssitant-backend

Kiểm tra server bằng `GET /health`. Khi deploy sau domain HTTPS, nhập URL dạng
`https://ten-mien-cua-ban` vào ô `API endpoint` trong popup. Extension sẽ tự
dùng `wss://` cho WebSocket; khi chạy local, giữ URL mặc định
`http://127.0.0.1:8000`.

## Cài extension

1. Mở chrome://extensions.
2. Bật Developer mode.
3. Chọn Load unpacked và chọn thư mục
   /Users/thai.nguyen/Projects/LiveTranslateAssitant/chrome_extension.
4. Mở tab có âm thanh, bấm icon LiveTranslateAssitant, chọn giọng đọc rồi bấm Bắt đầu.
   Gemini Live sẽ tự nhận diện ngôn ngữ nguồn.
5. Bấm Mở transcript để mở lại panel transcript trên tab đang chạy nếu panel
   đã bị ẩn.

Nút × trên panel chỉ đóng giao diện transcript; phiên capture, dịch và TTS vẫn
tiếp tục chạy. Chỉ nút Dừng trong popup mới kết thúc phiên.

Extension cần thao tác Bắt đầu bởi người dùng vì đây là điều kiện bảo mật của
Chrome cho tabCapture. Audio gốc được route lại qua loa với gain 1, vì vậy
người dùng nghe đồng thời audio gốc và tiếng dịch.

## API nội bộ

- GET /health — trạng thái Gemini Live và TTS.
- GET /api/voices — danh sách preset thainp-tts.
- POST /api/tts/live — smoke test TTS.
- WS /ws/live?source_lang=auto&voice_id=Ngọc%20Huyền%202.0 — nhận PCM16 mono
  16 kHz, trả JSON sự kiện rồi WAV binary. Tham số `source_lang` vẫn được giữ
  để tương thích API nội bộ, nhưng extension hiện luôn gửi `auto`.

## Lưu ý

- Không lưu audio cuộc họp mặc định.
- Gemini API key phải để ở backend, không đưa vào extension.
- thainp-tts sử dụng runtime TTS được tách từ
  Auto-Translade-video/src/vieneu; thông báo nguồn và license Apache 2.0
  được giữ tại backend/VENDORED_NOTICE.md và backend/VieNeu-TTS-LICENSE.txt.
