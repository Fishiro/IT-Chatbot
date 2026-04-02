export default async function handler(req, res) {
    if (req.method !== "POST") {
        return res.status(405).json({ error: "Method not allowed" });
    }

    try {
        const response = await fetch(
            "https://it-chatbot-lyg3.onrender.com/api/chat",
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(req.body),
            },
        );

        const data = await response.json();
        return res.status(response.status).json(data);
    } catch (error) {
        return res.status(502).json({ error: "Không thể kết nối Render." });
    }
}
