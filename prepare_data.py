import os
import time
import docx
import pandas as pd
from pptx import Presentation
from dotenv import load_dotenv

# Import các module của LangChain
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

# ==========================================
# HÀM HỖ TRỢ: ĐỌC ĐA ĐỊNH DẠNG TÀI LIỆU
# ==========================================


def extract_text_from_file(file_path):
    """Tự động nhận diện đuôi file và trích xuất văn bản"""
    ext = os.path.splitext(file_path)[1].lower()
    text = ""

    try:
        # Đọc file Word (.docx)
        if ext == '.docx':
            doc = docx.Document(file_path)
            text = "\n".join(
                [para.text for para in doc.paragraphs if para.text.strip()])

        # Đọc file PowerPoint (.pptx)
        elif ext == '.pptx':
            prs = Presentation(file_path)
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        text += shape.text + "\n"

        # Đọc file Excel (.xlsx)
        elif ext == '.xlsx':
            # Đọc toàn bộ các sheet trong file
            xls = pd.read_excel(file_path, sheet_name=None)
            for sheet_name, df in xls.items():
                text += f"\n--- Bảng: {sheet_name} ---\n"
                text += df.to_string(index=False) + "\n"

        else:
            print(f"    [!] Bỏ qua file định dạng không hỗ trợ: {file_path}")

    except Exception as e:
        print(f"    [X] Lỗi khi đọc file {file_path}: {e}")

    return text


# ==========================================
# BƯỚC 1: KHỞI TẠO MÔI TRƯỜNG
# ==========================================
load_dotenv()
my_api_key = os.getenv("GEMINI_API_KEY")

if not my_api_key:
    print("❌ Lỗi: Không tìm thấy GEMINI_API_KEY trong file .env")
    exit()

# ==========================================
# BƯỚC 2: QUÉT THƯ MỤC & NẠP DỮ LIỆU
# ==========================================
data_folder = "learning_data"
all_docs = []

print("1. Đang quét thư mục và nạp toàn bộ tài liệu...")
for filename in os.listdir(data_folder):
    file_path = os.path.join(data_folder, filename)

    # Chỉ xử lý nếu nó là file (bỏ qua thư mục con nếu có)
    if os.path.isfile(file_path):
        print(f" -> Đang trích xuất: {filename}")
        content = extract_text_from_file(file_path)

        if content.strip():
            # Lưu kèm metadata là tên file để sau này AI biết nó lấy từ nguồn nào
            all_docs.append(Document(page_content=content,
                            metadata={"source": filename}))

if not all_docs:
    print("❌ Không tìm thấy dữ liệu hợp lệ để xử lý!")
    exit()

# ==========================================
# BƯỚC 3: CẮT NHỎ VĂN BẢN (CHUNKING)
# ==========================================
print("\n2. Đang chia nhỏ toàn bộ văn bản...")
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000, chunk_overlap=200)
chunks = text_splitter.split_documents(all_docs)
total_chunks = len(chunks)

print(f" -> Tổng cộng đã chia thành {total_chunks} đoạn nhỏ.")

# Khởi tạo model Embeddings
embeddings = GoogleGenerativeAIEmbeddings(
    model="models/gemini-embedding-001",
    google_api_key=my_api_key
)

# ==========================================
# BƯỚC 4: TẠO VECTOR DB THEO LÔ (BATCHING)
# ==========================================
print("\n3. Đang tạo Vector DB (Chia đợt để tránh quá tải API)...")
vectorstore = None
batch_size = 90

for i in range(0, total_chunks, batch_size):
    batch = chunks[i: i + batch_size]
    current_batch_num = (i // batch_size) + 1
    total_batches = (total_chunks + batch_size - 1) // batch_size

    print(
        f" -> Đang xử lý đợt {current_batch_num}/{total_batches} (từ đoạn {i} đến {i + len(batch)})...")

    if vectorstore is None:
        vectorstore = FAISS.from_documents(batch, embeddings)
    else:
        temp_db = FAISS.from_documents(batch, embeddings)
        vectorstore.merge_from(temp_db)

    if i + batch_size < total_chunks:
        print("    [Zzz] Tạm nghỉ 60 giây cho hệ thống API 'thở'...")
        time.sleep(60)

# ==========================================
# BƯỚC 5: LƯU ĐÈ / CẬP NHẬT VECTOR DB
# ==========================================
print("\n4. Đang lưu kho dữ liệu vào thư mục nội bộ...")
vectorstore.save_local("faiss_index")
print("🎉 Xuất sắc! Toàn bộ Word, PowerPoint và Excel đã được nạp thành công vào não bộ AI.")
