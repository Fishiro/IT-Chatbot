import express from "express";
import cors from "cors";
import axios from "axios";
import rateLimit from "express-rate-limit";

const app = express();
const port = process.env.PORT || 3000;

// --- Cần thiết khi deploy sau reverse proxy (Render, v.v.) để
//     express-rate-limit nhận đúng IP thật của client thay vì IP proxy ---
app.set("trust proxy", 1);

app.use(express.json({ limit: "50kb" })); // chặn body quá lớn từ tầng ngoài cùng

// --- BẢO MẬT: Whitelist CORS thay vì mở toàn bộ ---
// Phải khớp với danh sách origins trong server.py
const ALLOWED_ORIGINS = [
    "https://giasutinhoccanban.tech",
    "https://it-chatbot.vercel.app",
    "http://localhost:3000",
    "http://localhost:5000",
    "http://localhost:5500",
];

app.use(
    cors({
        origin(origin, callback) {
            // Cho phép request không có Origin (curl trong cùng server,
            // health check nội bộ...) nhưng chặn origin lạ từ trình duyệt.
            if (!origin || ALLOWED_ORIGINS.includes(origin)) {
                callback(null, true);
            } else {
                callback(new Error("Origin không được phép (CORS)."));
            }
        },
    }),
);

app.use(express.static("public"));

// URL của máy chủ Python backend (đang chạy trên cổng 5000)
const PYTHON_BACKEND_URL = "http://localhost:5000/api/chat";

// --- BẢO MẬT: Giới hạn số request / IP để chống spam & bòn rút quota Gemini ---
const chatLimiter = rateLimit({
    windowMs: 60 * 1000, // 1 phút
    max: 15, // tối đa 15 request/phút/IP — chỉnh theo nhu cầu thực tế
    standardHeaders: true,
    legacyHeaders: false,
    message: {
        error: "Bạn gửi tin nhắn quá nhanh. Vui lòng chờ một chút rồi thử lại.",
    },
});

app.head("/health", (req, res) => res.sendStatus(200));
app.get("/health", (req, res) => {
    res.json({ status: "ok" });
});

app.post("/api/chat", chatLimiter, async (req, res) => {
    try {
        const { message, sessionID, captchaToken } = req.body || {};

        if (!message || !sessionID) {
            return res
                .status(400)
                .json({ error: "Thiếu 'message' hoặc 'sessionID'." });
        }

        // --- BẢO MẬT: Chặn message quá dài trước khi tốn quota Gemini ---
        if (typeof message !== "string" || message.length > 2000) {
            return res.status(400).json({
                error: "Tin nhắn quá dài (tối đa 2000 ký tự).",
            });
        }
        if (typeof sessionID !== "string" || sessionID.length > 100) {
            return res.status(400).json({ error: "sessionID không hợp lệ." });
        }

        console.log(
            `Node.js (Gateway) nhận được tin nhắn cho session: ${sessionID}`,
        );

        // Chuyển tiếp yêu cầu (kèm captchaToken) đến máy chủ Python — Python
        // là nơi thực sự xác minh captcha với Google trước khi gọi Gemini.
        const pythonResponse = await axios.post(
            PYTHON_BACKEND_URL,
            { message, sessionID, captchaToken },
            { timeout: 60_000 },
        );

        res.json(pythonResponse.data);
    } catch (error) {
        if (error.response) {
            // Lỗi đến từ máy chủ Python (ví dụ: lỗi 500, 400 từ Flask)
            // KHÔNG forward nguyên văn chi tiết lỗi nội bộ ra client.
            console.error(
                "Lỗi từ backend Python:",
                error.response.status,
                error.response.data,
            );
            res.status(error.response.status).json({
                error: "Có lỗi xảy ra khi xử lý yêu cầu. Vui lòng thử lại.",
            });
        } else if (error.request) {
            console.error(
                "Không thể kết nối đến máy chủ Python:",
                error.message,
            );
            res.status(502).json({
                error: "Bad Gateway - Không thể kết nối đến dịch vụ AI.",
            });
        } else {
            console.error("Lỗi không xác định:", error.message);
            res.status(500).json({
                error: "Có lỗi xảy ra phía máy chủ Gateway.",
            });
        }
    }
});

// --- Xử lý lỗi CORS bị chặn (từ middleware cors() phía trên) gọn gàng ---
app.use((err, req, res, next) => {
    if (err && err.message && err.message.includes("CORS")) {
        return res.status(403).json({ error: "Origin không được phép." });
    }
    next(err);
});

app.listen(port, "0.0.0.0", () => {
    console.log(
        `Máy chủ Node.js (Gateway) đang chạy tại http://localhost:${port}`,
    );
    console.log(
        `Đang chuyển tiếp yêu cầu đến backend Python tại ${PYTHON_BACKEND_URL}`,
    );
});
