import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv
import socket

load_dotenv()

app = Flask(__name__)
CORS(app)

# --- Cấu hình Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("Thiếu GEMINI_API_KEY. Hãy kiểm tra file .env của bạn.")

genai.configure(api_key=GEMINI_API_KEY)

system_instruction = """Bạn là một "Gia sư số Tin học".
Nhiệm vụ của bạn là giải thích các khái niệm tin học cơ bản
(phần cứng, phần mềm, internet, lập trình cơ bản, v.v.)
một cách đơn giản, dễ hiểu cho người mới bắt đầu.
Nhiệm vụ của bạn là trả lời ngắn gọn trừ trường hợp người dùng yêu cầu trả lời dài hơn.
Hãy kiên nhẫn và dùng ví dụ minh họa khi cần thiết.

Nếu người dùng hỏi nội dung không liên quan đến chủ đề và ngữ cảnh hiện tại, 
bạn có quyền không trả lời câu hỏi đó mà hãy trả lời "Tôi không được huấn luyện để trả lời nội dung này."
"""

config = genai.types.GenerationConfig(
    max_output_tokens=2048
)

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash-lite",
    system_instruction=system_instruction,
    generation_config=config
)

# --- Bộ nhớ Session (Python) ---
active_sessions = {}


@app.route("/api/chat", methods=["POST"])
def chat():
    try:
        data = request.json
        user_message = data.get("message")
        session_id = data.get("sessionID")

        if not user_message:
            return jsonify({"error": "Thiếu 'message' trong body."}), 400

        if not session_id:
            return jsonify({"error": "Thiếu 'sessionID' trong body."}), 400

        if session_id in active_sessions:
            chat = active_sessions[session_id]
        else:
            print(f"Tạo phiên chat mới: {session_id}")
            chat = model.start_chat(history=[])
            active_sessions[session_id] = chat

        response = chat.send_message(user_message)
        bot_message = response.text

        return jsonify({"reply": bot_message})

    except Exception as e:
        print(f"Lỗi phía máy chủ Python: {e}")
        return jsonify({"error": "Có lỗi xảy ra phía máy chủ Python"}), 500


def get_local_ip():
    """Lấy địa chỉ IP nội bộ của máy."""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Kết nối đến một địa chỉ IP công cộng (không thực sự gửi dữ liệu)
        # để buộc HĐH chọn đúng card mạng
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"  # Trả về localhost nếu không tìm thấy
    finally:
        if s:
            s.close()
    return ip


if __name__ == "__main__":
    local_ip = get_local_ip()
    node_port = 3000   # Cổng Frontend (Node.js) người dùng truy cập
    flask_port = 5000  # Cổng Backend (Python) này

    # Chiều rộng của nội dung bên trong bảng
    width = 60

    # Định nghĩa các dòng nội dung
    title1 = "  ► Máy chủ Python (Backend) đã sẵn sàng."
    line1_1 = f"     - Đang chạy tại: http://localhost:{flask_port}"
    line1_2 = f"     - Chấp nhận kết nối từ: 0.0.0.0:{flask_port}"

    title2 = "  ► ĐƯỜNG DẪN TRUY CẬP CHATBOT (cho mạng LAN):"
    line2_1 = f"     Mở trên máy này: http://localhost:{node_port}"
    line2_2 = f"     Mở trên thiết bị khác: http://{local_ip}:{node_port}"

    # In bảng
    print(f"╔{'═' * width}╗")
    print(f"║{title1.ljust(width)}║")
    print(f"║{line1_1.ljust(width)}║")
    print(f"║{line1_2.ljust(width)}║")
    print(f"╟{'─' * width}╢")  # Dấu phân cách
    print(f"║{title2.ljust(width)}║")
    print(f"║{line2_1.ljust(width)}║")
    print(f"║{line2_2.ljust(width)}║")
    print(f"╚{'═' * width}╝")

    # CHẠY MÁY CHỦ PYTHON
    app.run(host="0.0.0.0", port=flask_port, debug=True)
