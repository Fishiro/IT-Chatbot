import os
import threading
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.middleware.proxy_fix import ProxyFix
from google import genai
from google.genai import types
from dotenv import load_dotenv
import socket

# --- Import thêm thư viện cho Vector DB ---
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_community.vectorstores import FAISS

load_dotenv()

app = Flask(__name__)

# --- BẢO MẬT: Cần thiết khi deploy sau reverse proxy (Render, v.v.)
#     để Flask/Limiter nhận đúng IP thật của client (X-Forwarded-For)
#     thay vì luôn thấy IP nội bộ của proxy. Nếu thiếu dòng này,
#     rate limit theo IP sẽ KHÔNG hoạt động đúng trên Render. ---
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

CORS(app, origins=[
    "https://giasutinhoccanban.tech",
    "https://it-chatbot.vercel.app",
    "http://localhost:3000",
    "http://localhost:5000",
    "http://localhost:5500",
])

# --- BẢO MẬT: Giới hạn request/IP để chống spam & bòn rút quota Gemini.
#     Đây là lớp chặn quan trọng nhất vì Flask có thể bị gọi trực tiếp
#     (curl/Postman) bỏ qua hoàn toàn giao diện web hay Node gateway. ---
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],  # không áp mặc định toàn site, chỉ áp cho route cụ thể bên dưới
    storage_uri="memory://",  # đủ dùng cho 1 instance free-tier; không cần Redis
)

# --- Phục vụ Frontend tĩnh (dùng khi deploy trực tiếp Python, không qua Node gateway) ---
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    if path and os.path.exists(os.path.join("public", path)):
        return send_from_directory("public", path)
    return send_from_directory("public", "index.html")

# --- Cấu hình Gemini (Hỗ trợ Fallback) ---
api_keys_list = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2") # Bạn có thể để sẵn, nếu rỗng code sẽ tự bỏ qua
]

# Lọc ra danh sách các key hợp lệ (không bị None hoặc chuỗi rỗng)
VALID_API_KEYS = [k for k in api_keys_list if k]

if not VALID_API_KEYS:
    raise ValueError("Không tìm thấy bất kỳ GEMINI_API_KEY nào. Hãy kiểm tra file .env!")

# Khởi tạo sẵn các client tương ứng với từng key
clients = [genai.Client(api_key=key) for key in VALID_API_KEYS]
current_key_index = 0  # Biến toàn cục theo dõi key đang active

# --- Prompt Hệ thống ---
system_instruction = (
    "Bạn là 'Gia sư Tin học căn bản (TT 11/2018/TT-BLĐTBXH)'. Nhiệm vụ: Giải thích và hướng dẫn thực hành tin học chính xác.\n"
    "QUY TẮC:\n"
    "1. NGUỒN: Ưu tiên tối đa [Tài liệu tham khảo]. Chỉ dùng kiến thức nền nếu tài liệu thiếu. CẤM bịa đặt tính năng/phím tắt.\n"
    "2. TỪ CHỐI: Nếu ngoài phạm vi/thiếu dữ kiện, đáp đúng câu: 'Vấn đề này ngoài phạm vi Tin học căn bản hoặc thiếu thông tin. Vui lòng cung cấp thêm chi tiết.'\n"
    "3. CẤU TRÚC: Lý thuyết súc tích. Thực hành phải trình bày từng bước (1, 2, 3...) trọn vẹn từ bắt đầu đến kết thúc. Bắt buộc dùng bullet points hoặc số thứ tự.\n"
    "4. VĂN PHONG: Sư phạm, chuyên nghiệp, dùng chuẩn thuật ngữ, tuyệt đối không phản hồi ngắt quãng hay bỏ lửng."
)

config = types.GenerateContentConfig(
    max_output_tokens=550,
    temperature=0.15,
    top_p=0.15,
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
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
RAG_MIN_RELEVANCE = float(os.getenv("RAG_MIN_RELEVANCE", "0.55"))  # 0..1, càng cao càng chặt
MAX_ACTIVE_SESSIONS = int(os.getenv("MAX_ACTIVE_SESSIONS", "200"))
DEBUG_RAG = os.getenv("DEBUG_RAG", "0") == "1"


def load_vectorstore():
    global retriever, vectorstore_error
    print("🔄 [Background] Đang nạp Vector DB (faiss_index)...")
    try:
        embeddings = GoogleGenerativeAIEmbeddings(
            model="models/gemini-embedding-001",
            google_api_key=VALID_API_KEYS[0] 
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


MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "2000"))

# --- BẢO MẬT: reCAPTCHA v3 — xác minh "vô hình" ---
# Không hiện bất kỳ ô/tuỳ chọn nào cho người dùng. Frontend gọi
# grecaptcha.execute(...) trong lúc gõ để lấy token, gửi kèm request.
# Ở đây backend gọi Google để đổi token lấy điểm tin cậy (0.0 - 1.0),
# điểm càng cao càng chắc là người thật. Đây là lớp chặn tool tự động
# gọi thẳng vào /api/chat (curl/Postman/script) mà KHÔNG chạy qua trình
# duyệt thật — vì những công cụ đó không thể tạo ra token hợp lệ.
RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")
RECAPTCHA_MIN_SCORE = float(os.getenv("RECAPTCHA_MIN_SCORE", "0.5"))
RECAPTCHA_EXPECTED_ACTION = "chat"


def verify_captcha(token, remote_ip=None):
    """
    Trả True nếu request được coi là hợp lệ (cho phép đi tiếp).
    - Nếu chưa cấu hình RECAPTCHA_SECRET_KEY (vd đang chạy local dev)
      → bỏ qua việc kiểm tra, không chặn nhầm.
    - Nếu Google API lỗi tạm thời (mạng, timeout) → fail-open (cho qua),
      vì đây chỉ là 1 lớp phòng thủ bổ sung bên cạnh rate-limit, không
      phải lớp duy nhất — tránh chặn nhầm người dùng thật khi Google sập.
    """
    if not RECAPTCHA_SECRET_KEY:
        return True
    if not token:
        return False
    try:
        resp = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={
                "secret": RECAPTCHA_SECRET_KEY,
                "response": token,
                "remoteip": remote_ip,
            },
            timeout=5,
        )
        result = resp.json()
        return (
            result.get("success") is True
            and result.get("score", 0) >= RECAPTCHA_MIN_SCORE
            and result.get("action") == RECAPTCHA_EXPECTED_ACTION
        )
    except Exception as e:
        print(f"⚠️ [reCAPTCHA] Lỗi xác minh (fail-open, cho qua): {e}")
        return True


@app.route("/api/chat", methods=["POST"])
@limiter.limit("15 per minute")
def chat():
    global current_key_index # Khai báo để có thể thay đổi key đang dùng
    try:
        data = request.get_json(silent=True) or {}
        user_message = data.get("message")
        session_id = data.get("sessionID")
        captcha_token = data.get("captchaToken")

        if not user_message or not session_id:
            return jsonify({"error": "Thiếu dữ liệu."}), 400

        if not isinstance(user_message, str) or len(user_message) > MAX_MESSAGE_LENGTH:
            return jsonify({
                "error": f"Tin nhắn quá dài (tối đa {MAX_MESSAGE_LENGTH} ký tự)."
            }), 400
        if not isinstance(session_id, str) or len(session_id) > 100:
            return jsonify({"error": "sessionID không hợp lệ."}), 400

        if not verify_captcha(captcha_token, request.remote_addr):
            return jsonify({
                "error": "Xác minh bảo mật thất bại. Vui lòng tải lại trang và thử lại."
            }), 403

        # --- Giới hạn số session giữ trong RAM ---
        if session_id not in active_sessions and len(active_sessions) >= MAX_ACTIVE_SESSIONS:
            oldest = session_order.pop(0)
            active_sessions.pop(oldest, None)

        # --- Chờ Vector DB ---
        if not vectorstore_ready.is_set():
            vectorstore_ready.wait(timeout=RAG_MAX_WAIT_SECONDS)

        # --- Chuẩn bị RAG Context ---
        if retriever:
            context, has_relevant, sources = get_relevant_context(user_message)
            if has_relevant:
                augmented_message = (
                    f"[Tài liệu tham khảo]:\n{context}\n\n"
                    f"[Câu hỏi của tôi]: {user_message}"
                )
            else:
                augmented_message = (
                    "[Tài liệu tham khảo]: (không tìm thấy đoạn nào đủ liên quan)\n\n"
                    f"[Câu hỏi của tôi]: {user_message}"
                )
        else:
            augmented_message = user_message
            sources = []

        # ============================================================
        # CƠ CHẾ FALLBACK VÒNG LẶP
        # ============================================================
        max_retries = len(VALID_API_KEYS)
        
        for attempt in range(max_retries):
            try:
                # 1. Lấy hoặc tạo session với client HIỆN TẠI
                if session_id not in active_sessions:
                    chat_session = clients[current_key_index].chats.create(
                        model="gemini-flash-lite-latest",
                        config=config
                    )
                    active_sessions[session_id] = chat_session
                    session_order.append(session_id)
                else:
                    chat_session = active_sessions[session_id]

                # 2. Gửi tin nhắn
                response = chat_session.send_message(augmented_message)
                
                # 3. Thành công thì trả về ngay lập tức (thoát vòng lặp)
                result = {"reply": response.text}
                if DEBUG_RAG:
                    result["_debug_sources"] = sources
                return jsonify(result)

            except Exception as e:
                error_str = str(e).lower()
                # Kiểm tra xem lỗi có phải do hết Quota (429 Resource Exhausted) không
                if "429" in error_str or "quota" in error_str or "exhausted" in error_str:
                    print(f"⚠️ [Fallback] Key index {current_key_index} bị giới hạn. Đang chuyển sang key tiếp theo...")
                    
                    # Chuyển sang key kế tiếp (quay vòng tròn nếu hết mảng)
                    current_key_index = (current_key_index + 1) % len(VALID_API_KEYS)
                    
                    # BẢO TOÀN LỊCH SỬ CHAT: Chuyển lịch sử sang session thuộc Client/Key mới
                    if session_id in active_sessions:
                        try:
                            # Lấy lịch sử cũ bằng hàm của SDK mới
                            old_history = chat_session.get_history() 
                            # Tạo session mới đè lên cái cũ
                            active_sessions[session_id] = clients[current_key_index].chats.create(
                                model="gemini-flash-lite-latest",
                                config=config,
                                history=old_history
                            )
                        except Exception as hist_err:
                            print(f"⚠️ [Fallback] Không thể copy lịch sử: {hist_err}")
                            # Nếu copy lịch sử lỗi, xóa session để nó tạo mới hoàn toàn ở vòng lặp sau
                            active_sessions.pop(session_id, None) 
                            if session_id in session_order:
                                session_order.remove(session_id)
                    
                    # Tiếp tục vòng lặp for để thử lại với attempt mới
                    continue 
                else:
                    # Nếu là lỗi khác (như mạng rớt, model sập, lỗi code), ném ra để xử lý lỗi 500
                    raise e
                    
        # Nếu thoát khỏi vòng lặp mà vẫn chưa return, nghĩa là tất cả các key đều đã kiệt quệ
        return jsonify({
            "error": "Tất cả máy chủ AI đều đang quá tải (Hết hạn mức). Vui lòng quay lại vào ngày mai!"
        }), 503

    except Exception:
        import traceback
        print("--- LỖI CHI TIẾT TỪ SERVER (chỉ hiện trong log) ---")
        traceback.print_exc()
        return jsonify({
            "error": "Có lỗi xảy ra khi xử lý yêu cầu. Vui lòng thử lại sau."
        }), 500


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        "error": "Bạn gửi tin nhắn quá nhanh. Vui lòng chờ một chút rồi thử lại."
    }), 429


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
