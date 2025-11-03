import express from "express";
import cors from "cors";
import axios from "axios";

const app = express();
const port = 3000;

app.use(express.json());
app.use(cors());
app.use(express.static("public"));

// URL của máy chủ Python backend (đang chạy trên cổng 5000)
const PYTHON_BACKEND_URL = "http://localhost:5000/api/chat";

app.post("/api/chat", async (req, res) => {
    try {
        const { message, sessionID } = req.body;

        if (!message || !sessionID) {
            return res
                .status(400)
                .json({ error: "Thiếu 'message' hoặc 'sessionID'." });
        }

        console.log(
            `Node.js (Gateway) nhận được tin nhắn cho session: ${sessionID}`
        );

        // Chuyển tiếp yêu cầu đến máy chủ Python
        const pythonResponse = await axios.post(PYTHON_BACKEND_URL, {
            message: message,
            sessionID: sessionID,
        });

        const botReply = pythonResponse.data;

        res.json(botReply);
    } catch (error) {
        if (error.response) {
            // Lỗi đến từ máy chủ Python (ví dụ: lỗi 500, 400 từ Flask)
            console.error(
                "Lỗi từ backend Python:",
                error.response.status,
                error.response.data
            );
            res.status(error.response.status).json(error.response.data);
        } else if (error.request) {
            console.error(
                "Không thể kết nối đến máy chủ Python:",
                error.message
            );
            res.status(502).json({
                error: "Bad Gateway - Không thể kết nối đến dịch vụ AI.",
            });
        } else {
            console.error("Lỗi không xác định:", error.message);
            res.status(500).json({
                error: "Có lỗi xảy ra phía máy chủ Gateway",
            });
        }
    }
});

app.listen(port, "0.0.0.0", () => {
    console.log(
        `Máy chủ Node.js (Gateway) đang chạy tại http://localhost:${port}`
    );
    console.log(
        `Đang chuyển tiếp yêu cầu đến backend Python tại ${PYTHON_BACKEND_URL}`
    );
});
