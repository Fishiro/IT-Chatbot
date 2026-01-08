const sendBtn = document.getElementById("send-btn");
const userInput = document.getElementById("user-input");
const chatMessages = document.getElementById("chat-messages");

// URL trỏ đến backend server.js
const API_URL = "https://it-chatbot-lyg3.onrender.com/api/chat";
const sessionID = `session-${Date.now()}-${Math.random()
    .toString(36)
    .substring(2, 9)}`;

sendBtn.addEventListener("click", sendMessage);
userInput.addEventListener("keypress", function (e) {
    if (e.key === "Enter") {
        sendMessage();
    }
});

async function sendMessage() {
    const userMessage = userInput.value.trim();
    if (userMessage === "" || userInput.disabled) return; // Không gửi nếu đang gõ

    addMessageToChat("user", userMessage);
    userInput.value = "";

    // Vô hiệu hóa input khi bot đang "suy nghĩ"
    userInput.disabled = true;
    sendBtn.disabled = true;

    try {
        const response = await fetch(API_URL, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                message: userMessage,
                sessionID: sessionID,
            }),
        });

        if (!response.ok) {
            throw new Error("Lỗi kết nối đến máy chủ backend.");
        }

        const data = await response.json();
        const botMessage = data.reply;

        // Gọi hàm addMessageToChat với hiệu ứng gõ chữ (isTyping = true)
        addMessageToChat("bot", botMessage, true);
    } catch (error) {
        console.error("Lỗi khi gọi Backend:", error);
        addMessageToChat(
            "bot",
            "Xin lỗi, tôi đang gặp sự cố. Vui lòng thử lại sau."
        );
        // Bật lại input nếu có lỗi
        userInput.disabled = false;
        sendBtn.disabled = false;
        userInput.focus();
    } finally {
        // Không bật lại input ở đây, vì hàm gõ chữ sẽ tự bật
    }
}

/**
 * Nâng cấp hàm addMessageToChat
 * @param {string} sender "user" hoặc "bot"
 * @param {string} message Nội dung tin nhắn
 * @param {boolean} isTyping Bật hiệu ứng gõ chữ (chỉ cho bot)
 */
function addMessageToChat(sender, message, isTyping = false) {
    const messageElement = document.createElement("div");
    messageElement.classList.add("message", sender, "new-message");

    if (sender === "bot" && isTyping) {
        chatMessages.appendChild(messageElement);
        // Cuộn xuống khi tin nhắn bot bắt đầu xuất hiện
        chatMessages.scrollTop = chatMessages.scrollHeight;

        let i = 0;
        const speed = 20;

        function typeWriter() {
            // Kiểm tra xem người dùng có đang ở gần cuối không *TRƯỚC KHI* thêm chữ
            // (Thêm 10px làm "vùng đệm" cho chắc chắn)
            const isNearBottom =
                chatMessages.scrollHeight - chatMessages.clientHeight <=
                chatMessages.scrollTop + 10;

            if (i < message.length) {
                // Parse toàn bộ chuỗi con đảm bảo Markdown hợp lệ
                messageElement.innerHTML = marked.parse(
                    message.substring(0, i + 1)
                );
                i++;

                // Chỉ tự động cuộn nếu người dùng đang ở gần cuối
                if (isNearBottom) {
                    chatMessages.scrollTop = chatMessages.scrollHeight;
                }

                setTimeout(typeWriter, speed);
            } else {
                // Khi gõ xong
                messageElement.innerHTML = marked.parse(message);

                // Cuộn lần cuối nếu họ vẫn ở dưới
                if (isNearBottom) {
                    chatMessages.scrollTop = chatMessages.scrollHeight;
                }

                // Bật lại input và focus
                userInput.disabled = false;
                sendBtn.disabled = false;
                userInput.focus();
            }
        }
        typeWriter();
    } else {
        // Hiển thị ngay lập tức (cho User và cho Bot_Error)
        if (sender === "bot") {
            messageElement.innerHTML = marked.parse(message);
        } else {
            messageElement.innerHTML = message.replace(/\n/g, "<br>");
        }
        chatMessages.appendChild(messageElement);
        // Luôn cuộn xuống khi người dùng gửi tin
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
}
