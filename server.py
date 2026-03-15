import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from dotenv import load_dotenv
import socket

# --- Import thêm thư viện cho Vector DB ---
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_community.vectorstores import FAISS

load_dotenv()

app = Flask(__name__)
CORS(app)

# --- Cấu hình Gemini ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("Thiếu GEMINI_API_KEY. Hãy kiểm tra file .env của bạn.")

genai.configure(api_key=GEMINI_API_KEY)

# --- Nạp Vector DB (Bộ não kiến thức) ---
print("Đang nạp bộ nhớ Vector DB (faiss_index)...")
embeddings = GoogleGenerativeAIEmbeddings(
    model="models/gemini-embedding-001", 
    google_api_key=GEMINI_API_KEY
)

try:
    # allow_dangerous_deserialization=True là bắt buộc ở các phiên bản Langchain mới khi load file local
    vectorstore = FAISS.load_local("faiss_index", embeddings, allow_dangerous_deserialization=True)
    retriever = vectorstore.as_retriever(search_kwargs={"k": 3}) # Lấy 3 đoạn liên quan nhất
    print("-> Đã nạp thành công bộ não AI!")
except Exception as e:
    print(f"-> Không tìm thấy hoặc lỗi nạp Vector DB: {e}")
    retriever = None

# --- Prompt Hệ thống mới ---
system_instruction = """Bạn là một "Gia sư số Tin học căn bản theo Thông tư số 11/2018/TT-BLĐTBXH".
Nhiệm vụ của bạn là giải thích các khái niệm tin học cơ bản, tập trung vào tính chính xác, hướng dẫn thực hành từng bước, và dễ hiểu.

Hãy ưu tiên sử dụng thông tin từ phần [Tài liệu tham khảo] được cung cấp trong câu hỏi để trả lời. Nếu tài liệu tham khảo không có thông tin, hãy dùng kiến thức nền của bạn.
Hỏi đáp kiến thức lý thuyết súc tích. Hướng dẫn thao tác thực hành từng bước.
"""

config = genai.types.GenerationConfig(
    max_output_tokens=2048
)

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash-lite",
    system_instruction=system_instruction,
    generation_config=config
)

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
            chat_session = model.start_chat(history=[])
            active_sessions[session_id] = chat_session

        # --- Tích hợp tài liệu vào câu hỏi ---
        if retriever:
            # Lục tìm tài liệu
            docs = retriever.invoke(user_message)
            context = "\n\n".join([f"- Nội dung: {doc.page_content} \n(Nguồn: {doc.metadata.get('source', 'Không rõ')})" for doc in docs])
            
            # Gắn tài liệu vào sau lưng câu hỏi của người dùng một cách bí mật
            augmented_message = f"[Tài liệu tham khảo]:\n{context}\n\n[Câu hỏi của tôi]: {user_message}"
        else:
            augmented_message = user_message

        response = chat_session.send_message(augmented_message)
        return jsonify({"reply": response.text})

    except Exception as e:
        import traceback
        print("--- LỖI CHI TIẾT TỪ SERVER ---")
        traceback.print_exc() # Dòng này sẽ in ra chính xác thư viện nào bị thiếu hoặc lỗi
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

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)