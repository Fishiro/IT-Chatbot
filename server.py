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
system_instruction = (
    "Bạn là 'Gia sư số Tin học căn bản theo Thông tư số 11/2018/TT-BLĐTBXH'. "
    "Nhiệm vụ của bạn là giải thích các khái niệm tin học cơ bản, hướng dẫn thao tác thực hành từng bước, đảm bảo tính chính xác và dễ hiểu.\n\n"
    "QUY TẮC PHẢN HỒI NGHIÊM NGẶT:\n"
    "1. NGUỒN KIẾN THỨC: Ưu tiên tối đa việc sử dụng thông tin từ phần [Tài liệu tham khảo] được cung cấp trong câu hỏi. Nếu tài liệu tham khảo không có thông tin, bạn được phép dùng kiến thức nền để trả lời. TUYỆT ĐỐI KHÔNG tự sáng tạo (bịa đặt) các tính năng, phím tắt hoặc thao tác phần mềm không tồn tại.\n"
    "2. TỪ CHỐI NGOÀI PHẠM VI: Nếu câu hỏi không thuộc phạm vi Tin học căn bản hoặc bạn không có đủ dữ kiện để hướng dẫn chính xác, hãy từ chối ngay lập tức bằng câu:\n"
    "   'Tôi không tìm thấy thông tin hoặc vấn đề này nằm ngoài phạm vi Tin học căn bản. Xin vui lòng cung cấp thêm chi tiết hoặc tham khảo giáo trình chuyên sâu hơn.'\n"
    "3. CẤU TRÚC TRẢ LỜI: Trả lời lý thuyết phải súc tích. Hướng dẫn thao tác thực hành phải trình bày theo từng bước (step-by-step). Nếu nội dung dài, bắt buộc trình bày bằng danh sách gạch đầu dòng (bullet points) hoặc đánh số thứ tự để người dùng dễ đọc và làm theo.\n"
    "4. TÍNH HOÀN CHỈNH: Bạn phải viết câu trả lời đầy đủ, mạch lạc, không được bỏ lửng hoặc cắt ngang giữa chừng. Đảm bảo liệt kê từ bước bắt đầu đến khi hoàn thiện chu trình thao tác.\n"
    "5. VĂN PHONG: Mang tính sư phạm, chuyên nghiệp, ngắn gọn, dễ hiểu, sử dụng thuật ngữ tin học chính xác."
)

config = types.GenerateContentConfig(
    max_output_tokens=2048,
    system_instruction=system_instruction
)

# ============================================================
# NẠP VECTOR DB (FAISS) TRONG BACKGROUND THREAD
# → Flask bind port ngay lập tức, Render không bị timeout khi deploy
# → Request đến TRONG lúc đang nạp sẽ CHỜ (tối đa RAG_MAX_WAIT_SECONDS)
#   thay vì âm thầm bỏ qua RAG như trước — tránh trả lời "ngoài lề"
#   chỉ vì tới sớm sau khi server vừa cold-start (Render free hay sleep).
# → vectorstore chỉ tốn RAM (~35MB), không tốn CPU liên tục → nhẹ,
#   phù hợp free tier.
# ============================================================
retriever = None
vectorstore_ready = threading.Event()   # set() khi nạp XONG (thành công hay thất bại)
vectorstore_error = None

RAG_MAX_WAIT_SECONDS = float(os.getenv("RAG_MAX_WAIT_SECONDS", "55"))
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "4"))
RAG_MIN_RELEVANCE = float(os.getenv("RAG_MIN_RELEVANCE", "0.55"))  # 0..1, càng cao càng chặt
MAX_ACTIVE_SESSIONS = int(os.getenv("MAX_ACTIVE_SESSIONS", "200"))
DEBUG_RAG = os.getenv("DEBUG_RAG", "0") == "1"


def load_vectorstore():
    global retriever, vectorstore_error
    print("🔄 [Background] Đang nạp Vector DB (faiss_index)...")
    try:
        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=GEMINI_API_KEY
        )
        vectorstore = FAISS.load_local(
            "faiss_index", embeddings, allow_dangerous_deserialization=True
        )
        retriever = vectorstore
        print("✅ [Background] Đã nạp thành công bộ não AI!")
    except Exception as e:
        vectorstore_error = str(e)
        print(f"❌ [Background] Không tìm thấy hoặc lỗi nạp Vector DB: {e}")
        retriever = None
    finally:
        # Luôn set, kể cả khi lỗi — để các request đang chờ không bị treo mãi
        vectorstore_ready.set()


# Khởi động thread ngay khi app load — không block Flask
threading.Thread(target=load_vectorstore, daemon=True).start()

# ============================================================


def get_relevant_context(user_message: str):
    """
    Truy vấn FAISS, LỌC theo độ liên quan (relevance score) để loại bỏ
    những đoạn "gần nhất nhưng không thực sự liên quan" — nguyên nhân
    chính khiến model trả lời lạc đề dù đã có RAG.
    Trả về (context_text, has_relevant_docs, sources).
    """
    try:
        # similarity_search_with_relevance_scores trả score đã chuẩn hoá 0..1
        # (1 = liên quan nhất). An toàn hơn dùng score L2 thô.
        results = retriever.similarity_search_with_relevance_scores(
            user_message, k=RAG_TOP_K
        )
    except Exception as e:
        # Lỗi gọi embedding API (vd rate limit) không được làm sập cả request
        # -> chỉ bỏ qua RAG cho riêng câu hỏi này, không crash.
        print(f"⚠️ [RAG] Lỗi truy vấn vector DB, bỏ qua RAG cho câu này: {e}")
        return "", False, []

    good_docs = [(doc, score) for doc, score in results if score >= RAG_MIN_RELEVANCE]

    if DEBUG_RAG:
        print(f"🔎 [RAG] Câu hỏi: {user_message!r}")
        for doc, score in results:
            mark = "✅" if score >= RAG_MIN_RELEVANCE else "  "
            src = doc.metadata.get("source", "Không rõ")
            print(f"   {mark} score={score:.3f} src={src}")

    if not good_docs:
        return "", False, []

    context = "\n\n".join([
        f"- Nội dung: {doc.page_content}\n(Nguồn: {doc.metadata.get('source', 'Không rõ')})"
        for doc, _ in good_docs
    ])
    sources = [doc.metadata.get("source", "Không rõ") for doc, _ in good_docs]
    return context, True, sources


# FIX PING: Hỗ trợ cả GET lẫn HEAD (UptimeRobot dùng HEAD)
@app.route("/health", methods=["GET", "HEAD"])
def health():
    return jsonify({
        "status": "ok",
        "vectordb_ready": vectorstore_ready.is_set(),
        "vectordb_loaded": retriever is not None,
        "vectordb_error": vectorstore_error,
        "active_sessions": len(active_sessions),
    }), 200


active_sessions = {}
session_order = []  # FIFO để giới hạn RAM trên free tier


@app.route("/api/chat", methods=["POST"])
def chat():
    try:
        data = request.json
        user_message = data.get("message")
        session_id = data.get("sessionID")

        if not user_message or not session_id:
            return jsonify({"error": "Thiếu dữ liệu."}), 400

        # --- Giới hạn số session giữ trong RAM (free tier ~512MB) ---
        if session_id not in active_sessions and len(active_sessions) >= MAX_ACTIVE_SESSIONS:
            oldest = session_order.pop(0)
            active_sessions.pop(oldest, None)

        if session_id in active_sessions:
            chat_session = active_sessions[session_id]
        else:
            chat_session = client.chats.create(
                # FIX QUOTA: gemini-2.0-flash có 1500 req/ngày thay vì 20
                model="gemini-2.5-flash",
                config=config
            )
            active_sessions[session_id] = chat_session
            session_order.append(session_id)

        # --- Chờ Vector DB nạp xong (chỉ chờ ở những request đến sớm
        #     ngay sau cold-start; các request sau đó gần như tức thì) ---
        if not vectorstore_ready.is_set():
            vectorstore_ready.wait(timeout=RAG_MAX_WAIT_SECONDS)

        # --- Tích hợp RAG nếu Vector DB đã sẵn sàng ---
        if retriever:
            context, has_relevant, sources = get_relevant_context(user_message)
            if has_relevant:
                augmented_message = (
                    f"[Tài liệu tham khảo]:\n{context}\n\n"
                    f"[Câu hỏi của tôi]: {user_message}"
                )
            else:
                # Nói RÕ với model là không tìm thấy tài liệu liên quan,
                # để nó tuân theo QUY TẮC 2 (từ chối / dùng kiến thức nền
                # một cách có ý thức) thay vì âm thầm trộn lẫn.
                augmented_message = (
                    "[Tài liệu tham khảo]: (không tìm thấy đoạn nào đủ liên quan "
                    "trong giáo trình đã nạp)\n\n"
                    f"[Câu hỏi của tôi]: {user_message}"
                )
        else:
            augmented_message = user_message
            sources = []

        response = chat_session.send_message(augmented_message)
        result = {"reply": response.text}
        if DEBUG_RAG:
            result["_debug_sources"] = sources
        return jsonify(result)

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
