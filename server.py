import os
import time
import threading
import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from werkzeug.middleware.proxy_fix import ProxyFix
from google import genai
from google.genai import types
from dotenv import load_dotenv
import socket

# --- Import thêm thư viện cho Vector DB ---
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.embeddings import Embeddings

load_dotenv()

app = Flask(__name__)

# --- BẢO MẬT: Cần thiết khi deploy sau reverse proxy (Render, v.v.)
#     để Flask/Limiter nhận đúng IP thật của client (X-Forwarded-For)
#     thay vì luôn thấy IP nội bộ của proxy. Nếu thiếu dòng này,
#     rate limit theo IP sẽ KHÔNG hoạt động đúng trên Render. ---
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# --- BẢO MẬT: Talisman tự thêm các HTTP security header tương đương
#     helmet bên Node (X-Content-Type-Options, X-Frame-Options, HSTS...).
#     KIẾN TRÚC THỰC TẾ: server.js là entrypoint DUY NHẤT được public ra
#     ngoài (port do Render cấp qua $PORT); nó tự spawn server.py và chỉ
#     gọi Flask qua http://localhost:5000 — Flask KHÔNG public trực tiếp.
#     Vì vậy: helmet (Node) bảo vệ traffic thật từ người dùng; Talisman ở
#     đây chỉ là lớp phòng thủ bổ sung phòng trường hợp có ai gọi thẳng
#     vào Flask nội bộ (đã có INTERNAL_SECRET chặn thêm ở route /api/chat).
#     - content_security_policy=None: tắt CSP vì frontend (nếu Flask tự
#       phục vụ) có gọi CDN ngoài (Tailwind, reCAPTCHA, marked.js).
#     - force_https=False: BẮT BUỘC để False. Flask chỉ nhận request nội
#       bộ qua http://localhost:5000 (không có TLS, không có header
#       X-Forwarded-Proto). Nếu để True, Talisman sẽ redirect NGAY CẢ
#       cuộc gọi nội bộ của axios sang https://localhost:5000 — nơi Flask
#       không lắng nghe TLS — khiến MỌI request /api/chat qua gateway lỗi
#       502. HTTPS thật với người dùng đã do Node (helmet) + Render/edge
#       đảm nhiệm ở tầng ngoài.
Talisman(
    app,
    content_security_policy=None,
    force_https=False,
)

# --- BẢO MẬT: Giới hạn body size ở chính Flask, không chỉ dựa vào Node.
#     Nếu ai gọi thẳng Flask (bỏ qua Node gateway), body lớn vẫn bị chặn. ---
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024  # 50KB, khớp với giới hạn ở Node

# --- BẢO MẬT: Whitelist CORS thay vì mở toàn bộ ---
# Đọc từ biến môi trường ALLOWED_ORIGINS (phân tách bởi dấu phẩy) nếu có,
# để dùng CHUNG cấu hình với server.js — tránh lệch danh sách giữa 2 tầng
# khi thêm/sửa domain (chỉ cần set 1 biến env, không phải sửa 2 file).
# Nếu chưa set (vd dev local) thì fallback về danh sách mặc định như cũ.
_default_origins = [
    "https://giasutinhoccanban.tech",
    "https://it-chatbot.vercel.app",
    "http://localhost:3000",
    "http://localhost:5000",
    "http://localhost:5500",
    # Trình duyệt coi "localhost" và "127.0.0.1" là 2 origin KHÁC NHAU dù
    # cùng chạy trên máy local. Nhiều IDE (VS Code Simple Browser...) tự
    # mở bằng 127.0.0.1 thay vì localhost, nếu thiếu các dòng dưới đây
    # sẽ bị CORS chặn request /api/chat dù chạy đúng trên máy mình.
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5000",
    "http://127.0.0.1:5500",
]
_env_origins = os.getenv("ALLOWED_ORIGINS")
ALLOWED_ORIGINS = (
    [o.strip() for o in _env_origins.split(",") if o.strip()]
    if _env_origins
    else _default_origins
)

CORS(app, origins=ALLOWED_ORIGINS)

# --- BẢO MẬT: Giới hạn request/IP để chống spam & bòn rút quota Gemini.
#     Đây là lớp chặn quan trọng nhất vì Flask có thể bị gọi trực tiếp
#     (curl/Postman) bỏ qua hoàn toàn giao diện web hay Node gateway. ---
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],  # không áp mặc định toàn site, chỉ áp cho route cụ thể bên dưới
    storage_uri="memory://",  # đủ dùng cho 1 instance free-tier; không cần Redis
)

# --- Phục vụ Frontend tĩnh (dùng khi chạy `npm run start:py` một mình để
#     debug riêng backend Python, không đi qua Node gateway) ---


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    if path and os.path.exists(os.path.join("public", path)):
        return send_from_directory("public", path)
    return send_from_directory("public", "index.html")


# --- Cấu hình Gemini (Hỗ trợ Fallback) ---
api_keys_list = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2")
]

# Lọc ra danh sách các key hợp lệ (không bị None hoặc chuỗi rỗng)
VALID_API_KEYS = [k for k in api_keys_list if k]

if not VALID_API_KEYS:
    raise ValueError(
        "Không tìm thấy bất kỳ GEMINI_API_KEY nào. Hãy kiểm tra file .env!")

# Khởi tạo sẵn các client tương ứng với từng key
clients = [genai.Client(api_key=key) for key in VALID_API_KEYS]
current_key_index = 0  # Biến toàn cục theo dõi key đang active
# BẢO MẬT: tránh race condition khi nhiều request cùng fallback
key_index_lock = threading.Lock()

# --- BẢO MẬT: Secret nội bộ giữa Node gateway <-> Flask backend ---
# Nếu ai đó có URL trực tiếp của Flask (vd port 5000 lỡ public trên Render)
# thì vẫn không gọi được /api/chat nếu thiếu header bí mật này, vì chỉ có
# Node gateway biết secret (đọc từ cùng biến môi trường INTERNAL_SECRET).
# Nếu chưa set biến này (vd đang chạy dev local) thì bỏ qua kiểm tra.
# KHUYẾN NGHỊ: LUÔN set INTERNAL_SECRET trên production (Render) — đây là
# lớp chặn duy nhất ngăn ai đó bỏ qua toàn bộ CORS/rate-limit của Node
# nếu port của Flask vô tình lộ ra ngoài.
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET")

# --- BẢO MẬT: Ban tạm các IP xác minh captcha thất bại quá nhiều lần ---
# Đây là lớp chặn brute-force thêm bên cạnh rate-limit — nếu 1 IP fail
# captcha nhiều lần liên tiếp trong khoảng thời gian ngắn, có khả năng
# cao đó là bot/script chứ không phải người dùng thật.
failed_captcha_ips = {}  # {ip: (count, first_fail_timestamp)}
CAPTCHA_BAN_THRESHOLD = int(os.getenv("CAPTCHA_BAN_THRESHOLD", "5"))
CAPTCHA_BAN_WINDOW = int(os.getenv("CAPTCHA_BAN_WINDOW", "300"))  # giây


def is_ip_banned(ip):
    entry = failed_captcha_ips.get(ip)
    if not entry:
        return False
    count, first_time = entry
    if time.time() - first_time > CAPTCHA_BAN_WINDOW:
        failed_captcha_ips.pop(ip, None)
        return False
    return count >= CAPTCHA_BAN_THRESHOLD


def register_captcha_failure(ip):
    count, first_time = failed_captcha_ips.get(ip, (0, time.time()))
    failed_captcha_ips[ip] = (count + 1, first_time)


def reset_captcha_failures(ip):
    """Xóa bộ đếm fail sau khi IP xác minh captcha thành công."""
    failed_captcha_ips.pop(ip, None)


# --- TITLE LOCAL (KHÔNG GỌI AI / KHÔNG TẠO REQUEST PHỤ) ---
TITLE_STOPWORDS = {
    "xin", "chào", "bạn", "tôi", "mình", "cho", "giúp", "giúp tôi",
    "có", "thể", "được", "với", "một", "về", "này", "nhé",
    "ạ", "là", "thì", "đang", "vui", "lòng", "hỏi", "muốn", "muốn hỏi",
    "hãy", "theo", "sao", "thế", "nào", "bị", "trong"
}


def generate_local_title(message, max_words=7, max_chars=60):
    """Tạo tiêu đề nhanh bằng heuristic, không gọi Gemini và không tạo request phụ."""
    import re

    text = re.sub(r"\s+", " ", str(message or "")).strip()
    text = re.sub(r"^[\s\"'“”‘’]+|[\s\"'“”‘’]+$", "", text)
    if not text:
        return "Đoạn chat mới"

    # Bỏ các cụm mở đầu thường không mang ý nghĩa chủ đề.
    leading_patterns = [
        r"^(?:xin\s+chào[ ,:!-]*)",
        r"^(?:cho\s+(?:tôi|mình)\s+hỏi[ ,:!-]*)",
        r"^(?:mình|tôi)\s+muốn\s+hỏi[ ,:!-]*",
        r"^(?:bạn\s+)?có\s+thể\s+giúp(?:\s+(?:tôi|mình))?[ ,:!-]*",
        r"^(?:hãy\s+)?giúp(?:\s+(?:tôi|mình))?[ ,:!-]*",
        r"^(?:vui\s+lòng\s+)?hướng\s+dẫn[ ,:!-]*",
        r"^(?:cho\s+(?:tôi|mình)\s+)?biết[ ,:!-]*",
        r"^(?:làm\s+sao|làm\s+thế\s+nào)[ ,:!-]*",
        r"^cách[ ,:!-]+",
    ]
    for pattern in leading_patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)

    text = re.sub(r"[?!]+$", "", text).strip()
    words = text.split()

    # Giữ nguyên thứ tự để title vẫn tự nhiên, chỉ loại filler ngắn.
    filtered = []
    for word in words:
        clean = re.sub(r"^[^\wÀ-ỹ]+|[^\wÀ-ỹ./:+#_-]+$",
                       "", word, flags=re.UNICODE)
        if not clean:
            continue
        normalized = clean.lower()
        if normalized in TITLE_STOPWORDS and len(words) > 5:
            continue
        filtered.append(clean)
        if len(filtered) >= max_words:
            break

    title = " ".join(filtered).strip(" -:;,.!?/")
    if not title:
        title = " ".join(words[:max_words]).strip(" -:;,.!?/")

    if len(title) > max_chars:
        title = title[:max_chars].rsplit(" ", 1)[0].rstrip(" -:;,.!?/")

    if title:
        title = title[0].upper() + title[1:]
    return title or "Đoạn chat mới"


# --- SMALL-TALK / META INTENT (KHÔNG CẦN RAG) ---
# Các câu chào hỏi, cảm ơn hoặc hỏi chatbot có thể làm gì không phải
# câu hỏi kiến thức. Không đưa chúng qua FAISS vì rất dễ không đạt
# RAG_MIN_RELEVANCE và bị system prompt buộc từ chối.
def detect_small_talk(user_message: str):
    """Trả về câu trả lời trực tiếp cho small-talk, hoặc None nếu không phải."""
    import re

    text = re.sub(r"\s+", " ", str(user_message or "")).strip().lower()
    normalized = re.sub(r"[!?.,;:]+", " ", text)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    # Chỉ chào hỏi khi toàn bộ câu thực sự là lời chào.
    greetings = {
        "xin chào", "chào", "hello", "hi", "hey", "chào bạn",
        "xin chào ai", "chào ai", "hello ai", "hi ai",
    }
    if normalized in greetings:
        return (
            "Xin chào! Tôi là Gia sư Tin học căn bản. "
            "Tôi có thể giúp bạn học và thực hành các nội dung tin học "
            "cơ bản như Windows, Word, Excel, PowerPoint, Internet và các thao tác máy tính."
        )

    # Hỏi về khả năng/chức năng của chính chatbot.
    capability_patterns = [
        r"^bạn (?:có thể|có khả năng|làm được|giúp được|hỗ trợ) (?:tôi )?(?:làm )?gì$",
        r"^bạn (?:có thể|có khả năng|làm được|giúp được|hỗ trợ) gì$",
        r"^bạn có thể giúp gì(?: cho tôi)?$",
        r"^bạn giúp được gì(?: cho tôi)?$",
        r"^bạn hỗ trợ được gì(?: cho tôi)?$",
        r"^bạn làm được những gì$",
        r"^bạn có những chức năng gì$",
        r"^chức năng của bạn là gì$",
        r"^bạn là ai$",
    ]
    if any(re.fullmatch(pattern, normalized, flags=re.IGNORECASE)
           for pattern in capability_patterns):
        return (
            "Tôi là Gia sư Tin học căn bản. Tôi tập trung hỗ trợ học và thực hành "
            "tin học cơ bản, chẳng hạn như Windows, Word, Excel, PowerPoint, "
            "Internet và các thao tác máy tính. Bạn chỉ cần gửi câu hỏi hoặc "
            "mô tả vấn đề cần giải quyết."
        )

    # Cảm ơn/kết thúc hội thoại đơn giản.
    thanks = {
        "cảm ơn", "cảm ơn bạn", "cảm ơn ai", "thanks", "thank you",
        "ok cảm ơn", "được rồi cảm ơn", "cảm ơn nhé", "cảm ơn nha",
    }
    if normalized in thanks:
        return "Không có gì! Khi cần hỗ trợ về Tin học căn bản, bạn cứ gửi câu hỏi cho tôi."

    return None


# --- Prompt Hệ thống ---
system_instruction = (
    "Bạn là 'Gia sư Tin học căn bản (TT 11/2018/TT-BLĐTBXH)'. Nhiệm vụ: Giải thích và hướng dẫn thực hành tin học chính xác.\n"
    "QUY TẮC:\n"
    "1. NGUỒN: Phần [Kiến thức nền] (nếu có) là tri thức NỘI BỘ bạn đã nắm sẵn từ trước — TUYỆT ĐỐI KHÔNG nói các câu kiểu "
    "'dựa trên tài liệu bạn cung cấp/vừa gửi', 'theo tài liệu bạn đưa', 'dựa vào file bạn tải lên'. Người dùng KHÔNG hề gửi "
    "tài liệu nào trong lượt chat — hãy trả lời thẳng vào nội dung như một chuyên gia đã am hiểu sẵn, không nhắc đến nguồn "
    "hay quá trình bạn lấy thông tin ở đâu. Nếu [Kiến thức nền] trống hoặc thiếu, TUYỆT ĐỐI KHÔNG dùng kiến thức nền của bạn để trả lời và chỉ phản hồi lại 'Vấn đề này ngoài phạm vi Tin học căn bản hoặc thiếu thông tin. Vui lòng cung cấp thêm chi tiết.', trả lời trong "
    "ngắn gọn trong phạm vi dưới 550 token. CẤM bịa đặt tính năng/phím tắt.\n"
    "2. TỪ CHỐI: Nếu ngoài phạm vi/thiếu dữ kiện, đáp đúng câu: 'Vấn đề này ngoài phạm vi Tin học căn bản hoặc thiếu thông tin. Vui lòng cung cấp thêm chi tiết.'\n"
    "3. CẤU TRÚC: Lý thuyết súc tích. Thực hành phải trình bày từng bước (1, 2, 3...) trọn vẹn từ bắt đầu đến kết thúc. Bắt buộc dùng bullet points hoặc số thứ tự.\n"
    "4. VĂN PHONG: Sư phạm, chuyên nghiệp, tự tin như người đã am hiểu sẵn kiến thức, dùng chuẩn thuật ngữ, tuyệt đối không phản hồi ngắt quãng hay bỏ lửng."
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
# set() khi nạp XONG (thành công hay thất bại)
vectorstore_ready = threading.Event()
vectorstore_error = None

RAG_MAX_WAIT_SECONDS = float(os.getenv("RAG_MAX_WAIT_SECONDS", "55"))
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
# 0..1, càng cao càng chặt
RAG_MIN_RELEVANCE = float(os.getenv("RAG_MIN_RELEVANCE", "0.55"))
MAX_ACTIVE_SESSIONS = int(os.getenv("MAX_ACTIVE_SESSIONS", "200"))
DEBUG_RAG = os.getenv("DEBUG_RAG", "0") == "1"


# ============================================================
# WRAPPER FALLBACK CHO EMBEDDING MODEL (RAG)
# → Model embedding (dùng để tìm tài liệu liên quan) TRƯỚC ĐÂY chỉ
#   dùng cố định VALID_API_KEYS[0]. Nếu key này hết quota, RAG bị
#   âm thầm bỏ qua (chatbot trả lời "chay", không có tài liệu tham
#   khảo) dù key thứ 2 vẫn còn hạn mức — đây là lỗ hổng đã fix ở đây.
# → Wrapper này thử lần lượt từng key trong danh sách mỗi khi gọi
#   embed_query/embed_documents, xoay vòng giống hệt cơ chế fallback
#   của model chat bên dưới.
# ============================================================
class FallbackEmbeddings(Embeddings):
    # QUAN TRỌNG: PHẢI kế thừa langchain_core.embeddings.Embeddings.
    # FAISS dùng isinstance(embedding_function, Embeddings) để quyết định
    # gọi .embed_query()/.embed_documents() (đúng) hay gọi trực tiếp
    # embedding_function(text) như một hàm cũ (deprecated, và class này
    # không có __call__ nên sẽ ném lỗi "object is not callable"). Thiếu
    # dòng kế thừa này khiến RAG bị âm thầm vô hiệu ở MỌI câu hỏi.
    def __init__(self, api_keys, model="models/gemini-embedding-001"):
        self._embedders = [
            GoogleGenerativeAIEmbeddings(model=model, google_api_key=k)
            for k in api_keys
        ]
        self._index = 0
        self._lock = threading.Lock()

    def _call_with_fallback(self, method_name, *args, **kwargs):
        last_err = None
        start = self._index
        for offset in range(len(self._embedders)):
            idx = (start + offset) % len(self._embedders)
            try:
                result = getattr(self._embedders[idx], method_name)(
                    *args, **kwargs)
                if idx != self._index:
                    with self._lock:
                        self._index = idx
                    print(
                        f"⚠️ [Embedding Fallback] Đã chuyển embedding sang key index {idx}")
                return result
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "quota" in err_str or "exhausted" in err_str:
                    print(
                        f"⚠️ [Embedding Fallback] Key embedding index {idx} bị giới hạn, thử key kế tiếp...")
                    last_err = e
                    continue
                # lỗi khác (không phải quota) thì ném ra luôn, không thử key khác
                raise
        raise last_err

    # LangChain gọi 2 hàm này khi nạp FAISS và khi truy vấn similarity search
    def embed_query(self, text):
        return self._call_with_fallback("embed_query", text)

    def embed_documents(self, texts):
        return self._call_with_fallback("embed_documents", texts)


def load_vectorstore():
    global retriever, vectorstore_error
    print("🔄 [Background] Đang nạp Vector DB (faiss_index)...")
    try:
        embeddings = FallbackEmbeddings(VALID_API_KEYS)
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

    good_docs = [(doc, score)
                 for doc, score in results if score >= RAG_MIN_RELEVANCE]

    if DEBUG_RAG:
        print(f"🔎 [RAG] Câu hỏi: {user_message!r}")
        for doc, score in results:
            mark = "✅" if score >= RAG_MIN_RELEVANCE else "  "
            src = doc.metadata.get("source", "Không rõ")
            print(f"   {mark} score={score:.3f} src={src}")

    if not good_docs:
        return "", False, []

    context = "\n\n".join([doc.page_content for doc, _ in good_docs])
    sources = [doc.metadata.get("source", "Không rõ") for doc, _ in good_docs]
    return context, True, sources


# FIX PING: Hỗ trợ cả GET lẫn HEAD (UptimeRobot dùng HEAD)
# --- BẢO MẬT: /health CỐ TÌNH để mở, KHÔNG yêu cầu captcha/secret,
#     vì UptimeRobot và Render health-check cần gọi được tự do để giữ
#     server không bị sleep / biết trạng thái deploy. Chỉ ẩn bớt chi
#     tiết lỗi nội bộ (vectordb_error) khỏi người gọi thường, để không
#     lộ đường dẫn file/nội dung lỗi ra ngoài. ---
@app.route("/health", methods=["GET", "HEAD"])
def health():
    is_internal = bool(INTERNAL_SECRET) and request.headers.get(
        "X-Internal-Secret") == INTERNAL_SECRET
    payload = {
        "status": "ok",
        "vectordb_ready": vectorstore_ready.is_set(),
        "vectordb_loaded": retriever is not None,
        "active_sessions": len(active_sessions),
    }
    if is_internal:
        payload["vectordb_error"] = vectorstore_error
    return jsonify(payload), 200


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
    Xác minh reCAPTCHA v3 và log đầy đủ lý do thành công/thất bại.

    Không log token/secret để tránh lộ thông tin bảo mật.
    """

    print("\n" + "═" * 64)
    print("🔐 [CAPTCHA] XÁC MINH REQUEST")
    print(f"   IP        : {remote_ip or 'UNKNOWN'}")
    print(f"   Token     : {'PRESENT' if token else 'MISSING'}")
    print(f"   Threshold : {RECAPTCHA_MIN_SCORE}")
    print(f"   Expected  : {RECAPTCHA_EXPECTED_ACTION}")
    print("═" * 64)

    # Local development: không cấu hình secret thì bỏ qua CAPTCHA
    if not RECAPTCHA_SECRET_KEY:
        print("🟡 [CAPTCHA] SKIP")
        print("   Lý do     : RECAPTCHA_SECRET_KEY chưa được cấu hình")
        print("   Chế độ    : FAIL-OPEN / LOCAL DEV")
        print("═" * 64)
        return True

    # Không có token
    if not token:
        print("🔴 [CAPTCHA] FAIL")
        print("   Reason    : TOKEN_MISSING")
        print("═" * 64)
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

        success = result.get("success")
        score = result.get("score")
        action = result.get("action")
        hostname = result.get("hostname")
        challenge_ts = result.get("challenge_ts")
        error_codes = result.get("error-codes", [])

        print("📡 [CAPTCHA] GOOGLE RESPONSE")
        print(f"   HTTP      : {resp.status_code}")
        print(f"   success   : {success}")
        print(f"   score     : {score}")
        print(f"   action    : {action}")
        print(f"   expected  : {RECAPTCHA_EXPECTED_ACTION}")
        print(f"   hostname  : {hostname}")
        print(f"   timestamp : {challenge_ts}")
        print(f"   errors    : {error_codes}")

        # Phân tích lý do fail
        reasons = []

        if success is not True:
            reasons.append("GOOGLE_SUCCESS_FALSE")

        if score is None:
            reasons.append("SCORE_MISSING")
        elif score < RECAPTCHA_MIN_SCORE:
            reasons.append("SCORE_TOO_LOW")

        if action != RECAPTCHA_EXPECTED_ACTION:
            reasons.append("ACTION_MISMATCH")

        if reasons:
            print("🔴 [CAPTCHA] FAIL")
            print(f"   Reason    : {', '.join(reasons)}")
            print("═" * 64)
            return False

        print("🟢 [CAPTCHA] PASS")
        print("   Reason    : ALL_CHECKS_PASSED")
        print("═" * 64)

        return True

    except requests.exceptions.Timeout:
        print("⚠️ [reCAPTCHA] TIMEOUT")
        print("   Google không phản hồi trong 5 giây")
        print("   Chế độ    : FAIL-OPEN")
        print("═" * 64)
        return True

    except requests.exceptions.RequestException as e:
        print("⚠️ [reCAPTCHA] NETWORK ERROR")
        print(f"   Error     : {e}")
        print("   Chế độ    : FAIL-OPEN")
        print("═" * 64)
        return True

    except Exception as e:
        print("⚠️ [reCAPTCHA] UNEXPECTED ERROR")
        print(f"   Type      : {type(e).__name__}")
        print(f"   Error     : {e}")
        print("   Chế độ    : FAIL-OPEN")
        print("═" * 64)
        return True


@app.route("/api/chat", methods=["POST"])
@limiter.limit("15 per minute")
def chat():
    global current_key_index  # Khai báo để có thể thay đổi key đang dùng
    try:
        # --- BẢO MẬT: Chỉ chấp nhận request đến từ Node gateway (biết secret) ---
        if INTERNAL_SECRET and request.headers.get("X-Internal-Secret") != INTERNAL_SECRET:
            return jsonify({"error": "Không có quyền truy cập."}), 403

        client_ip = request.remote_addr

        # --- BẢO MẬT: Chặn IP đang bị ban tạm do fail captcha nhiều lần ---
        if is_ip_banned(client_ip):
            return jsonify({
                "error": "IP của bạn tạm thời bị chặn do xác minh bảo mật thất bại nhiều lần. Vui lòng thử lại sau."
            }), 429

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

        if not verify_captcha(captcha_token, client_ip):
            register_captcha_failure(client_ip)
            return jsonify({
                "error": "Xác minh bảo mật thất bại. Vui lòng tải lại trang và thử lại."
            }), 403

        # CAPTCHA hợp lệ: reset bộ đếm lỗi cũ của IP. Nếu không reset,
        # một vài lỗi captcha rải rác trong 5 phút có thể cộng dồn và
        # cuối cùng ban nhầm người dùng thật.
        reset_captcha_failures(client_ip)

        # --- Small-talk không cần RAG/Gemini ---
        # Tránh trường hợp câu chào/hỏi chức năng không có tài liệu FAISS
        # rồi bị system prompt buộc trả lời "ngoài phạm vi".
        is_new_session = session_id not in active_sessions
        small_talk_reply = detect_small_talk(user_message)
        if small_talk_reply is not None:
            result = {"reply": small_talk_reply}
            if is_new_session:
                result["title"] = generate_local_title(user_message)
            if DEBUG_RAG:
                result["_debug_sources"] = []
            print("💬 [Small-talk] Bỏ qua RAG/Gemini cho câu hỏi xã giao/meta.")
            return jsonify(result)

        # Session chưa tồn tại nghĩa là đây là tin nhắn đầu tiên của đoạn chat.
        # Title sẽ được tạo local và trả cùng response, không có request AI thứ 2.

        # --- Giới hạn số session giữ trong RAM ---
        if is_new_session and len(active_sessions) >= MAX_ACTIVE_SESSIONS:
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
                    f"[Kiến thức nền]:\n{context}\n\n"
                    f"[Câu hỏi của tôi]: {user_message}"
                )
            else:
                augmented_message = (
                    "[Kiến thức nền]: (không có — hãy dùng kiến thức nền sẵn có của bạn)\n\n"
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
                if is_new_session:
                    result["title"] = generate_local_title(user_message)
                if DEBUG_RAG:
                    result["_debug_sources"] = sources
                return jsonify(result)

            except Exception as e:
                error_str = str(e).lower()
                # Kiểm tra xem lỗi có phải do hết Quota (429 Resource Exhausted) không
                if "429" in error_str or "quota" in error_str or "exhausted" in error_str:
                    print(
                        f"⚠️ [Fallback] Key index {current_key_index} bị giới hạn. Đang chuyển sang key tiếp theo...")

                    # Chuyển sang key kế tiếp (quay vòng tròn nếu hết mảng)
                    # BẢO MẬT/AN TOÀN: dùng lock để tránh 2 request đồng thời
                    # cùng đổi current_key_index chồng lên nhau (race condition).
                    with key_index_lock:
                        current_key_index = (
                            current_key_index + 1) % len(VALID_API_KEYS)

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
                            print(
                                f"⚠️ [Fallback] Không thể copy lịch sử: {hist_err}")
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
