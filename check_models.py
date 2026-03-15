import os
from google import genai
from dotenv import load_dotenv

# Tải API Key từ file .env
load_dotenv()

# Khởi tạo kết nối với Google
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

print("Đang truy xuất danh sách model từ Google...")
print("-" * 30)

# Lọc và in ra những model có chữ "embedding" trong tên
for model in client.models.list():
    if "embedding" in model.name.lower():
        print(f"Tên model khả dụng: {model.name}")

print("-" * 30)
