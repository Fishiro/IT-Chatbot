from google import genai

client = genai.Client()

print("Danh sách các model chuyên dụng cho Chat/Văn bản và hạn mức token:")
print("=" * 60)

for model in client.models.list():
    methods = getattr(model, "supported_generation_methods", [])

    model_id = model.name.lower()

    print(f"• Model ID: {model.name}")
    print(f"  Tên hiển thị : {getattr(model, 'display_name', 'N/A')}")
    print(f"  Input Token  : {getattr(model, 'input_token_limit', 'N/A'):,}" if isinstance(getattr(
        model, 'input_token_limit', None), int) else f"  Input Token  : {getattr(model, 'input_token_limit', 'N/A')}")
    print(f"  Output Token : {getattr(model, 'output_token_limit', 'N/A'):,}" if isinstance(getattr(
        model, 'output_token_limit', None), int) else f"  Output Token : {getattr(model, 'output_token_limit', 'N/A')}")
    print("-" * 60)
