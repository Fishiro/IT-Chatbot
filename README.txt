Yêu cầu Python 3.11.0

Tạo file .env đưa key API của Gemini vào dự án trước khi chạy
GEMINI_API_KEY=
                ^^^^^^^^^^

Đầu tiên chạy setup cho lần đầu (1 lần duy nhất): npm install

CÀI THƯ VIỆN PHỤ THUỘC
python -m pip install -r requirements.txt
npm install

CHẠY SERVER (YÊU CẦU ĐÃ SETUP XONG)
1. Kích hoạt env Python: python-env\Scripts\Activate.ps1
2. Khởi động server: npm run dev

DÙNG NGROK MỞ PUBLIC LINK TEST
ngrok http 3000

HƯỚNG DẪN PHỤ
deactive (hủy kích hoạt env)
Tổ hợp phím Ctrl C (ngắt lệnh, ngưng server...)
