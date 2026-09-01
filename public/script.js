const sendBtn = document.getElementById("send-btn");
const userInput = document.getElementById("user-input");
const chatMessages = document.getElementById("chat-messages");
const historyList = document.getElementById("history-list");
const newChatBtn = document.getElementById("new-chat-btn");
const inputWrapper = document.getElementById("input-wrapper");

const API_URL =
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1"
        ? "http://localhost:5000/api/chat"
        : "/api/chat";

// --- BẢO MẬT: phải TRÙNG với Site Key đã điền trong index.html ---
const RECAPTCHA_SITE_KEY = "6Lc_1aItAAAAAB-QDCxy0BpLBn-uyLqoni7MvEdL";

// Lấy token reCAPTCHA v3 (vô hình — không cần user thao tác gì).
// Nếu vì lý do nào đó reCAPTCHA chưa load được (mạng chậm, bị chặn quảng
// cáo...) thì trả về null, để không làm gãy trải nghiệm gửi tin nhắn —
// backend sẽ tự quyết định chặn hay không khi thiếu token.
async function getCaptchaToken(action = "chat") {
    try {
        if (typeof grecaptcha === "undefined" || !grecaptcha?.execute) {
            return null;
        }
        return await new Promise((resolve) => {
            grecaptcha.ready(() => {
                grecaptcha
                    .execute(RECAPTCHA_SITE_KEY, { action })
                    .then(resolve)
                    .catch(() => resolve(null));
            });
        });
    } catch {
        return null;
    }
}

// --- QUẢN LÝ TRẠNG THÁI ---
let currentAbortController = null;
let currentTypingTimeout = null; // BIẾN MỚI: Dùng để quản lý tiến trình gõ phím của bot
let sessions = {};
let currentSessionID = generateSessionID();
let currentTitle = "Đoạn chat mới";
let isFirstMessage = true;

// Khởi tạo session mặc định đầu tiên
sessions[currentSessionID] = { title: currentTitle, messages: [] };

function generateSessionID() {
    return `session-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
}

sendBtn.addEventListener("click", sendMessage);

// Textarea: Enter gửi, Shift+Enter xuống dòng
userInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// ---- AUTO-RESIZE & SHOW/HIDE SEND BTN (MOBILE) ----
function autoResizeTextarea() {
    userInput.style.height = "auto";
    const isMobile = window.innerWidth < 768;
    if (isMobile) {
        userInput.style.height = Math.min(userInput.scrollHeight, 130) + "px";
        if (userInput.value.trim().length > 0) {
            inputWrapper.classList.add("has-text");
        } else {
            inputWrapper.classList.remove("has-text");
        }
    } else {
        userInput.style.height = userInput.scrollHeight + "px";
    }
}

userInput.addEventListener("input", autoResizeTextarea);

window.addEventListener("resize", () => {
    autoResizeTextarea();
    if (window.innerWidth >= 768) {
        inputWrapper.classList.remove("has-text");
    }
});

// --- HÀM GỬI TIN NHẮN ---
async function sendMessage() {
    const message = userInput.value.trim();
    if (!message) return;

    // Lưu tin nhắn của user vào session
    sessions[currentSessionID].messages.push({ sender: "user", text: message });
    addMessageToChat("user", message);

    // Reset input
    userInput.value = "";
    autoResizeTextarea();

    // Biến cờ cục bộ để biết đây có phải là tin đầu tiên không
    let wasFirstMessage = false;

    // Thiết lập UI nếu là tin nhắn đầu
    if (isFirstMessage) {
        isFirstMessage = false;
        wasFirstMessage = true;

        // Hiện ngay đoạn chat lên Sidebar với tên trích xuất tạm từ tin nhắn
        let tempTitle = message.substring(0, 25);
        if (message.length > 25) tempTitle += "...";
        currentTitle = tempTitle;
        sessions[currentSessionID].title = currentTitle;
        addSessionToHistoryUI(currentSessionID, currentTitle);

        // LƯU Ý: Không gọi generateAITitle ở đây nữa để tránh làm chết Server Python cục bộ
    }

    // Khóa UI & Hiển thị indicator
    if (typeof showThinkingIndicator === "function") showThinkingIndicator();
    userInput.disabled = true;
    sendBtn.disabled = true;

    // Hủy request trước đó nếu đang chạy ngầm
    if (currentAbortController) {
        currentAbortController.abort();
    }
    currentAbortController = new AbortController();
    const signal = currentAbortController.signal;

    // Hủy hiệu ứng gõ chữ cũ nếu có
    if (currentTypingTimeout) {
        clearTimeout(currentTypingTimeout);
        currentTypingTimeout = null;
    }

    try {
        // BẢO MẬT: lấy token reCAPTCHA v3 vô hình trước khi gửi — không có
        // bước thao tác nào của người dùng, chạy nền trong lúc gõ.
        const captchaToken = await getCaptchaToken("chat");

        const response = await fetch(API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            // THÊM SESSION ID ĐỂ BACKEND NHỚ LỊCH SỬ CHAT
            body: JSON.stringify({
                message: message,
                sessionID: currentSessionID,
                captchaToken: captchaToken,
            }),
            signal: signal,
        });

        const data = await response.json();

        // Lưu tin nhắn của AI vào session
        sessions[currentSessionID].messages.push({
            sender: "bot",
            text: data.reply,
        });

        // Tắt indicator
        if (typeof hideThinkingIndicator === "function")
            hideThinkingIndicator();
        document
            .querySelectorAll(".typing-indicator")
            .forEach((el) => el.remove());

        // Hiển thị tin nhắn chính
        addMessageToChat("bot", data.reply, true);

        // SAU KHI AI ĐÃ TRẢ LỜI XONG, BÂY GIỜ MỚI GỌI API ĐỂ LẤY TITLE
        if (wasFirstMessage) {
            generateAITitle(message, currentSessionID);
        }
    } catch (error) {
        if (error.name === "AbortError") {
            console.log("Đã hủy request cũ do người dùng chuyển session.");
        } else {
            console.error("Lỗi khi gọi AI:", error);
            if (typeof hideThinkingIndicator === "function")
                hideThinkingIndicator();
            document
                .querySelectorAll(".typing-indicator")
                .forEach((el) => el.remove());
            addMessageToChat("bot", "Xin lỗi, đã có lỗi kết nối xảy ra.");

            // Mở khóa UI do lỗi
            userInput.disabled = false;
            sendBtn.disabled = false;
        }
    }
}

// --- HÀM LOAD LỊCH SỬ CHAT ---
function loadChatHistory(sessionId) {
    // 1. Hủy ngay lập tức API đang gọi dở dang
    if (currentAbortController) {
        currentAbortController.abort();
        currentAbortController = null;
    }

    // 2. ÉP BUỘC HỦY hiệu ứng gõ chữ ngầm (Tránh Memory Leak & giật UI)
    if (currentTypingTimeout) {
        clearTimeout(currentTypingTimeout);
        currentTypingTimeout = null;
    }

    // 3. ÉP BUỘC TẮT thanh "AI đang suy nghĩ"
    if (typeof hideThinkingIndicator === "function") hideThinkingIndicator();
    document
        .querySelectorAll(".typing-indicator, #thinking-indicator")
        .forEach((el) => el.remove());

    // 4. ÉP BUỘC MỞ KHÓA ô chat và nút Gửi
    userInput.disabled = false;
    sendBtn.disabled = false;
    newChatBtn.disabled = false;
    newChatBtn.classList.remove("opacity-50", "pointer-events-none");

    // 5. Nếu người dùng đang nhắn dở đoạn chat hiện tại mà chưa lưu, thì lưu tạm vào UI
    if (
        currentSessionID !== sessionId &&
        sessions[currentSessionID] &&
        sessions[currentSessionID].messages.length > 0
    ) {
        addSessionToHistoryUI(
            currentSessionID,
            sessions[currentSessionID].title,
        );
    }

    // 6. Chuyển đổi ID và Title
    currentSessionID = sessionId;
    currentTitle = sessions[sessionId].title;
    isFirstMessage = false;

    // 7. Highlight nút active ở Sidebar
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
    const activeBtn = document.getElementById(`hist-${sessionId}`);
    if (activeBtn) {
        activeBtn.classList.add(
            "bg-gray-200",
            "dark:bg-gray-700/50",
            "ring-1",
            "ring-gray-300",
            "dark:ring-gray-600",
            "font-medium",
        );
    }

    // 8. Xóa khung chat cũ và vẽ lại toàn bộ tin nhắn (không hiệu ứng gõ máy)
    chatMessages.innerHTML = "";
    sessions[sessionId].messages.forEach((msg) => {
        addMessageToChat(msg.sender, msg.text, false);
    });

    // Đẩy thanh cuộn xuống cuối
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// --- HÀM GẮN NÚT LỊCH SỬ LÊN SIDEBAR ---
function addSessionToHistoryUI(id, title) {
    if (document.getElementById(`hist-${id}`)) {
        // Cập nhật lại title nếu nút đã tồn tại
        const existingBtn = document.getElementById(`hist-${id}`);
        if (existingBtn.querySelector(".hist-title")) {
            existingBtn.querySelector(".hist-title").textContent = title;
        }
        return;
    }

    const btn = document.createElement("button");
    btn.id = `hist-${id}`;
    btn.className =
        "history-item w-full text-left px-3 py-2 rounded-lg text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700/50 truncate transition-all duration-200 flex items-center gap-2";

    btn.innerHTML = `
        <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4 shrink-0 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
        </svg>
        <span class="hist-title truncate">${title}</span>
    `;

    btn.addEventListener("click", () => {
        loadChatHistory(id);
    });

    historyList.prepend(btn);

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

// --- XỬ LÝ KHI NHẤN NÚT "+ ĐOẠN CHAT MỚI" ---
newChatBtn.addEventListener("click", () => {
    if (
        sessions[currentSessionID] &&
        sessions[currentSessionID].messages.length > 0
    ) {
        addSessionToHistoryUI(
            currentSessionID,
            sessions[currentSessionID].title,
        );
    }

    // Reset data
    currentSessionID = generateSessionID();
    currentTitle = "Đoạn chat mới";
    isFirstMessage = true;
    sessions[currentSessionID] = { title: currentTitle, messages: [] };

    // Hủy request cũ, hủy hiệu ứng gõ chữ, reset UI
    if (currentAbortController) {
        currentAbortController.abort();
        currentAbortController = null;
    }
    if (currentTypingTimeout) {
        clearTimeout(currentTypingTimeout);
        currentTypingTimeout = null;
    }

    if (typeof hideThinkingIndicator === "function") hideThinkingIndicator();
    document
        .querySelectorAll(".typing-indicator, #thinking-indicator")
        .forEach((el) => el.remove());

    userInput.disabled = false;
    sendBtn.disabled = false;

    // Reset Giao diện
    chatMessages.innerHTML = "";
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

// --- TẠO TITLE CHO ĐOẠN CHAT (ĐÃ FIX RACE CONDITION) ---
async function generateAITitle(firstMessage, targetSessionID) {
    try {
        const titleSession = "title-gen-" + targetSessionID;
        const prompt = `Đọc câu sau và đặt 1 tiêu đề thật ngắn gọn (tối đa 5 chữ) tóm tắt nội dung. Chỉ trả về đúng dòng tiêu đề, không giải thích, không dùng ngoặc kép: "${firstMessage}"`;

        const response = await fetch(API_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: prompt, sessionID: titleSession }),
        });

        const data = await response.json();
        let aiTitle = data.reply.trim().replace(/^["']|["']$/g, "");

        // Chỉ cập nhật dữ liệu của đúng session được yêu cầu
        if (sessions[targetSessionID]) {
            sessions[targetSessionID].title = aiTitle;

            // Nếu người dùng VẪN đang ở đoạn chat này, thì cập nhật biến hiện tại
            if (currentSessionID === targetSessionID) {
                currentTitle = aiTitle;
            }
        }

        // Cập nhật DOM trên thanh sidebar
        const histBtn = document.getElementById(`hist-${targetSessionID}`);
        if (histBtn) {
            const titleSpan = histBtn.querySelector(".hist-title");
            if (titleSpan) titleSpan.textContent = aiTitle;
        }
    } catch (error) {
        let fallbackTitle = firstMessage.substring(0, 20) + "...";
        if (sessions[targetSessionID]) {
            sessions[targetSessionID].title = fallbackTitle;
            if (currentSessionID === targetSessionID)
                currentTitle = fallbackTitle;
        }
    }
}

// --- HÀM RENDER TIN NHẮN ---
function addMessageToChat(sender, message, isTyping = false) {
    const messageElement = document.createElement("div");
    messageElement.classList.add("message", sender, "new-message");

    if (sender === "bot" && isTyping) {
        chatMessages.appendChild(messageElement);
        chatMessages.scrollTop = chatMessages.scrollHeight;

        let i = 0;
        const speed = 8; // ms giữa mỗi lần "nhả" chữ (giảm để nhanh hơn nữa)
        const charsPerTick = 6; // số ký tự nhả ra mỗi lần (tăng để nhanh hơn nữa)
        messageElement.textContent = "";

        function typeWriter() {
            // Kiểm tra xem user có đang ở cuối thanh cuộn không
            const isNearBottom =
                chatMessages.scrollHeight - chatMessages.clientHeight <=
                chatMessages.scrollTop + 10;

            if (i < message.length) {
                messageElement.textContent += message.slice(
                    i,
                    i + charsPerTick,
                );
                i += charsPerTick;
                if (isNearBottom)
                    chatMessages.scrollTop = chatMessages.scrollHeight;

                // Lưu tham chiếu timeout để có thể ngắt nếu người dùng chuyển tab
                currentTypingTimeout = setTimeout(typeWriter, speed);
            } else {
                // Kết thúc gõ phím -> parse Markdown và NHẢ KHÓA
                messageElement.innerHTML = marked.parse(message);
                if (isNearBottom)
                    chatMessages.scrollTop = chatMessages.scrollHeight;

                currentTypingTimeout = null; // Xóa tham chiếu
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
