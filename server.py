import os
import threading
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from google import genai
from google.genai import types
from dotenv import load_dotenv
import socket

# --- Import thêm thư viện cho Vector DB ---
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_community.vectorstores import FAISS

load_dotenv()

app = Flask(__name__)
CORS(app, origins=[
    "https://giasutinhoccanban.tech",
    "https://it-chatbot.vercel.app",
    "http://localhost:3000",
    "http://localhost:5000",
    "http://localhost:5500",
])

# --- Phục vụ Frontend tĩnh (dùng khi deploy trực tiếp Python, không qua Node gateway) ---
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    if path and os.path.exists(os.path.join("public", path)):
        return send_from_directory("public", path)
    return send_from_directory("public", "index.html")

# --- Cấu hình Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("Thiếu GEMINI_API_KEY. Hãy kiểm tra file .env của bạn.")

client = genai.Client(api_key=GEMINI_API_KEY)

# --- Prompt Hệ thống ---
system_instruction = """Bạn là một "Gia sư số Tin học căn bản theo Thông tư số 11/2018/TT-BLĐTBXH".
Nhiệm vụ của bạn là giải thích các khái niệm tin học cơ bản, tập trung vào tính chính xác, hướng dẫn thực hành từng bước, và dễ hiểu.

Hãy ưu tiên sử dụng thông tin từ phần [Tài liệu tham khảo] được cung cấp trong câu hỏi để trả lời. Nếu tài liệu tham khảo không có thông tin, hãy dùng kiến thức nền của bạn.
Hỏi đáp kiến thức lý thuyết súc tích. Hướng dẫn thao tác thực hành từng bước.
"""

config = types.GenerateContentConfig(
    max_output_tokens=2048,
    system_instruction=system_instruction
)

# ============================================================
# FIX DEPLOY: Load FAISS trong background thread
# → Flask bind port ngay lập tức, Render không bị timeout
# → Các request đầu tiên dùng kiến thức nền Gemini (retriever=None)
# → Sau ~30-60s FAISS sẵn sàng, các request sau dùng RAG đầy đủ
# ============================================================
retriever = None

def load_vectorstore():
    global retriever
    print("🔄 [Background] Đang nạp Vector DB (faiss_index)...")
    try:
        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=GEMINI_API_KEY
        )
        vectorstore = FAISS.load_local(
            "faiss_index", embeddings, allow_dangerous_deserialization=True
        )
        retriever = vectorstore.as_retriever(search_kwargs={"k": 3})
        print("✅ [Background] Đã nạp thành công bộ não AI!")
    except Exception as e:
        print(f"❌ [Background] Không tìm thấy hoặc lỗi nạp Vector DB: {e}")
        retriever = None

# Khởi động thread ngay khi app load — không block Flask
threading.Thread(target=load_vectorstore, daemon=True).start()

# ============================================================

# FIX PING: Hỗ trợ cả GET lẫn HEAD (UptimeRobot dùng HEAD)
@app.route("/health", methods=["GET", "HEAD"])
def health():
    return jsonify({"status": "ok", "vectordb": retriever is not None}), 200

active_sessions = {}

@app.route("/api/chat", methods=["POST"])
def chat():
    try:
        data = request.json
        user_message = data.get("message")
        session_id = data.get("sessionID")

        if not user_message or not session_id:
            return jsonify({"error": "Thiếu dữ liệu."}), 400

        if session_id in active_sessions:
            chat_session = active_sessions[session_id]
        else:
            chat_session = client.chats.create(
                # FIX QUOTA: gemini-2.0-flash có 1500 req/ngày thay vì 20
                model="gemini-2.5-flash",
                config=config
            )
            active_sessions[session_id] = chat_session

        # --- Tích hợp RAG nếu Vector DB đã sẵn sàng ---
        if retriever:
            docs = retriever.invoke(user_message)
            context = "\n\n".join([
                f"- Nội dung: {doc.page_content} \n(Nguồn: {doc.metadata.get('source', 'Không rõ')})"
                for doc in docs
            ])
            augmented_message = f"[Tài liệu tham khảo]:\n{context}\n\n[Câu hỏi của tôi]: {user_message}"
        else:
            augmented_message = user_message

        response = chat_session.send_message(augmented_message)
        return jsonify({"reply": response.text})

    except Exception as e:
        import traceback
        print("--- LỖI CHI TIẾT TỪ SERVER ---")
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def get_local_ip():
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        if s:
            s.close()
    return ip


if __name__ == "__main__":
    local_ip = get_local_ip()
    node_port = 3000
    flask_port = 5000

    width = 60
    title1 = "  ► Máy chủ Python (Backend) đã sẵn sàng."
    line1_1 = f"     - Đang chạy tại: http://localhost:{flask_port}"
    line1_2 = f"     - Chấp nhận kết nối từ: 0.0.0.0:{flask_port}"
    title2 = "  ► ĐƯỜNG DẪN TRUY CẬP CHATBOT:"
    line2_1 = f"     Mở trên máy này: http://localhost:{node_port}"
    line2_2 = f"     Mở thiết bị khác: http://{local_ip}:{node_port}"

    print(f"╔{'═' * width}╗")
    print(f"║{title1.ljust(width)}║")
    print(f"║{line1_1.ljust(width)}║")
    print(f"║{line1_2.ljust(width)}║")
    print(f"╟{'─' * width}╢")
    print(f"║{title2.ljust(width)}║")
    print(f"║{line2_1.ljust(width)}║")
    print(f"║{line2_2.ljust(width)}║")
    print(f"╚{'═' * width}╝")

    app.run(host="0.0.0.0", port=flask_port, debug=False)