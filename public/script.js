const sendBtn = document.getElementById("send-btn");
const userInput = document.getElementById("user-input");
const chatMessages = document.getElementById("chat-messages");
const historyList = document.getElementById("history-list");
const newChatBtn = document.getElementById("new-chat-btn");

const API_URL =
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1"
        ? "http://localhost:3000/api/chat"
        : "https://it-chatbot-lyg3.onrender.com/api/chat";

// --- QUẢN LÝ TRẠNG THÁI (Lưu trong phiên, F5 là mất) ---
let sessions = {}; // Cấu trúc: { id: { title: "...", messages: [ {sender, text} ] } }
let currentSessionID = generateSessionID();
let currentTitle = "Đoạn chat mới";
let isFirstMessage = true;

function generateSessionID() {
    return `session-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
}

sendBtn.addEventListener("click", sendMessage);
userInput.addEventListener("keypress", function (e) {
    if (e.key === "Enter") {
        sendMessage();
    }
});

async function sendMessage() {
    const userMessage = userInput.value.trim();
    if (userMessage === "" || userInput.disabled) return;

    if (!sessions[currentSessionID]) {
        sessions[currentSessionID] = { title: currentTitle, messages: [] };
    }

    addMessageToChat("user", userMessage);
    sessions[currentSessionID].messages.push({
        sender: "user",
        text: userMessage,
    });
    userInput.value = "";

    userInput.disabled = true;
    sendBtn.disabled = true;
    newChatBtn.disabled = true;
    newChatBtn.classList.add("opacity-50", "pointer-events-none");

    if (isFirstMessage) {
        isFirstMessage = false;
        generateAITitle(userMessage);
    }

    // ✅ THÊM VÀO ĐÂY — hiện thông báo trước khi fetch
    const warmingMsg = document.querySelector(".warming-notice");
    if (!warmingMsg) {
        const notice = document.createElement("div");
        notice.className = "message bot new-message warming-notice";
        notice.textContent = "⏳ Đang kết nối máy chủ, vui lòng chờ...";
        chatMessages.appendChild(notice);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    try {
        const response = await fetch(API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message: userMessage,
                sessionID: currentSessionID,
            }),
            signal: AbortSignal.timeout(60000),
        });

        // ✅ XÓA thông báo chờ khi đã có phản hồi
        document.querySelector(".warming-notice")?.remove();

        if (!response.ok) throw new Error("Lỗi kết nối đến máy chủ backend.");

        const data = await response.json();
        const botMessage = data.reply;

        addMessageToChat("bot", botMessage, true);
        sessions[currentSessionID].messages.push({
            sender: "bot",
            text: botMessage,
        });

        // ✅ Tự động lưu session vào sidebar ngay sau lần bot trả lời đầu tiên
        const isFirstBotReply =
            sessions[currentSessionID].messages.filter(
                (m) => m.sender === "bot",
            ).length === 1;
        if (isFirstBotReply) {
            addSessionToHistoryUI(
                currentSessionID,
                sessions[currentSessionID].title,
            );
        }
    } catch (error) {
        // ✅ XÓA thông báo chờ nếu lỗi
        document.querySelector(".warming-notice")?.remove();

        console.error("Lỗi:", error);
        const errMsg =
            error.name === "TimeoutError"
                ? "Máy chủ đang khởi động (cold start), vui lòng gửi lại sau 30 giây."
                : "Xin lỗi, tôi đang gặp sự cố. Vui lòng thử lại sau.";
        addMessageToChat("bot", errMsg);
        userInput.disabled = false;
        sendBtn.disabled = false;
        newChatBtn.disabled = false;
        newChatBtn.classList.remove("opacity-50", "pointer-events-none");
        userInput.focus();
    }
}

// Hàm gửi API ngầm để lấy tiêu đề
async function generateAITitle(firstMessage) {
    try {
        // Dùng 1 session_id khác để không làm rác lịch sử chat chính
        const titleSession = "title-gen-" + currentSessionID;
        const prompt = `Đọc câu sau và đặt 1 tiêu đề thật ngắn gọn (tối đa 5 chữ) tóm tắt nội dung. Chỉ trả về đúng dòng tiêu đề, không giải thích, không dùng ngoặc kép: "${firstMessage}"`;

        const response = await fetch(API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: prompt, sessionID: titleSession }),
        });

        const data = await response.json();
        let aiTitle = data.reply.trim().replace(/^["']|["']$/g, ""); // Bỏ ngoặc kép nếu AI cố tình trả về

        // Cập nhật lại tên session
        currentTitle = aiTitle;
        sessions[currentSessionID].title = currentTitle;

        // Cập nhật tên nút trong sidebar (nếu đã có)
        const histBtn = document.getElementById(`hist-${currentSessionID}`);
        if (histBtn) {
            histBtn.querySelector(".hist-title")
                ? (histBtn.querySelector(".hist-title").textContent =
                      currentTitle)
                : (histBtn.textContent = currentTitle);
        }
    } catch (error) {
        // Fallback: Lấy 20 ký tự đầu nếu API lỗi
        currentTitle = firstMessage.substring(0, 20) + "...";
        sessions[currentSessionID].title = currentTitle;
    }
}

// --- XỬ LÝ KHI NHẤN NÚT "+ ĐOẠN CHAT MỚI" ---
newChatBtn.addEventListener("click", () => {
    // Chỉ lưu vào lịch sử nếu phiên hiện tại CÓ tin nhắn
    if (
        sessions[currentSessionID] &&
        sessions[currentSessionID].messages.length > 0
    ) {
        addSessionToHistoryUI(
            currentSessionID,
            sessions[currentSessionID].title,
        );
    }

    // Reset mọi thứ về ban đầu
    currentSessionID = generateSessionID();
    currentTitle = "Đoạn chat mới";
    isFirstMessage = true;

    // Xóa giao diện khung chat hiện tại
    chatMessages.innerHTML = "";

    // Gỡ highlight của tất cả nút trong lịch sử
    document
        .querySelectorAll(".history-item")
        .forEach((b) =>
            b.classList.remove(
                "bg-gray-200",
                "dark:bg-gray-700/50",
                "ring-1",
                "ring-gray-300",
                "dark:ring-gray-600",
                "font-medium",
            ),
        );

    addMessageToChat(
        "bot",
        "Đã bắt đầu đoạn chat mới. Tôi có thể giúp gì cho bạn?",
    );
});

// Hàm gắn nút lịch sử mới vào Sidebar
function addSessionToHistoryUI(id, title) {
    // Ngăn chặn tạo trùng nút
    if (document.getElementById(`hist-${id}`)) return;

    const btn = document.createElement("button");
    btn.id = `hist-${id}`;
    btn.className =
        "history-item w-full text-left px-3 py-2 rounded-lg text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700/50 truncate transition-all duration-200 flex items-center gap-2";

    // Icon cuộc hội thoại
    btn.innerHTML = `
        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 shrink-0 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
        <span class="hist-title truncate">${title}</span>
    `;

    // Highlight nút active
    const setActive = () => {
        document
            .querySelectorAll(".history-item")
            .forEach((b) =>
                b.classList.remove(
                    "bg-gray-200",
                    "dark:bg-gray-700/50",
                    "ring-1",
                    "ring-gray-300",
                    "dark:ring-gray-600",
                    "font-medium",
                ),
            );
        btn.classList.add(
            "bg-gray-200",
            "dark:bg-gray-700/50",
            "ring-1",
            "ring-gray-300",
            "dark:ring-gray-600",
            "font-medium",
        );
    };

    btn.addEventListener("click", () => {
        setActive();

        // Nếu người dùng đang nhắn dở đoạn chat hiện tại mà chưa lưu, thì lưu tạm vào UI
        if (
            currentSessionID !== id &&
            sessions[currentSessionID] &&
            sessions[currentSessionID].messages.length > 0
        ) {
            addSessionToHistoryUI(
                currentSessionID,
                sessions[currentSessionID].title,
            );
        }

        // Khôi phục trạng thái sang đoạn chat cũ
        currentSessionID = id;
        currentTitle = sessions[id].title;
        isFirstMessage = false;

        // Vẽ lại toàn bộ tin nhắn cũ
        chatMessages.innerHTML = "";
        sessions[id].messages.forEach((msg) => {
            addMessageToChat(msg.sender, msg.text, false);
        });
    });

    historyList.prepend(btn); // Đẩy lên trên cùng của danh sách

    // Highlight ngay nếu đây là session đang active
    if (id === currentSessionID) {
        document
            .querySelectorAll(".history-item")
            .forEach((b) =>
                b.classList.remove(
                    "bg-gray-200",
                    "dark:bg-gray-700/50",
                    "ring-1",
                    "ring-gray-300",
                    "dark:ring-gray-600",
                    "font-medium",
                ),
            );
        btn.classList.add(
            "bg-gray-200",
            "dark:bg-gray-700/50",
            "ring-1",
            "ring-gray-300",
            "dark:ring-gray-600",
            "font-medium",
        );
    }
}

// Hàm render UI tin nhắn (Đã fix lỗi gõ Markdown)
function addMessageToChat(sender, message, isTyping = false) {
    const messageElement = document.createElement("div");
    messageElement.classList.add("message", sender, "new-message");

    if (sender === "bot" && isTyping) {
        chatMessages.appendChild(messageElement);
        chatMessages.scrollTop = chatMessages.scrollHeight;

        let i = 0;
        const speed = 20;
        messageElement.textContent = "";

        function typeWriter() {
            const isNearBottom =
                chatMessages.scrollHeight - chatMessages.clientHeight <=
                chatMessages.scrollTop + 10;

            if (i < message.length) {
                messageElement.textContent += message[i];
                i++;
                if (isNearBottom)
                    chatMessages.scrollTop = chatMessages.scrollHeight;
                setTimeout(typeWriter, speed);
            } else {
                messageElement.innerHTML = marked.parse(message);
                if (isNearBottom)
                    chatMessages.scrollTop = chatMessages.scrollHeight;
                userInput.disabled = false;
                sendBtn.disabled = false;
                newChatBtn.disabled = false;
                newChatBtn.classList.remove(
                    "opacity-50",
                    "pointer-events-none",
                );
                userInput.focus();
            }
        }
        typeWriter();
    } else {
        if (sender === "bot") {
            messageElement.innerHTML = marked.parse(message);
        } else {
            messageElement.textContent = message;
        }
        chatMessages.appendChild(messageElement);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
}

// ### Minh họa quy trình hoạt động (ASCII):

// Bạn hãy xem sơ đồ nhỏ này để hiểu tư duy phân luồng (Async/Await) của tính năng chúng ta vừa làm nhé:

// ```text
//        [Người dùng gửi tin nhắn đầu tiên]
//                     |
//            +--------+--------+ (Tách ra 2 luồng độc lập)
//            |                 |
//   [Trả lời câu hỏi]  [Gửi API ngầm lấy Title]
//            |                 |
//     Bot đang gõ...   Nhận Title: "Hàm VLOOKUP"
//            |                 |
//            +--------+--------+
//                     |
//       [Nhấn Nút: "+ Đoạn chat mới"]
//                     |
//   +-------------------------------------+
//   | Lấy Title "Hàm VLOOKUP"             |
//   | Lấy toàn bộ Array mảng tin nhắn     |
//   | -> Nhét lên Sidebar làm nút lịch sử |
//   +-------------------------------------+
//                     |
//            [Tạo khung chat Rỗng]

// Với cấu trúc trên, bạn có thể nhảy qua nhảy lại giữa các cuộc hội thoại cũ một cách hoàn hảo mà không hề bị làm rác file log ở Backend! Quí hãy dán vào và F5 trải nghiệm thử xem. Nếu cần tinh chỉnh thêm bất kì hiệu ứng chuyển động nào để giao diện "ảo diệu" hơn, cứ bảo mình nhé! Mọi cố gắng của Quí chắc chắn sẽ tạo ra một đồ án tuyệt đẹp!
