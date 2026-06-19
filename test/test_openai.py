import httpx
import json
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("BEDROCK_API_KEY")
print(api_key)
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}

data = {
    "model": "openai.gpt-oss-20b-1:0",
    "messages": [{"role": "user", "content": "Say hello world"}]
}

response = httpx.post(
    "https://bedrock-runtime.us-east-1.amazonaws.com/v1/chat/completions",
    headers=headers,
    json=data
)

print(response.status_code)
print(response.text)
