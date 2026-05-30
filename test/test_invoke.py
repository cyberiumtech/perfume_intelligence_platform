import boto3
import json
import os
from dotenv import load_dotenv

load_dotenv()

client = boto3.client(
    'bedrock-runtime', 
    region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
)

models_to_try = [
    "openai.gpt-oss-20b-1:0"
]

for m in models_to_try:
    try:
        messages = [{"role": "user", "content": [{"text": "You are a product data normalizer. Extract json. \n\n Input: Acqua Di Gio. Output ONLY JSON and nothing else."}]}]
        response = client.converse(
            modelId=m,
            messages=messages
        )
        import json
        with open('response.json', 'w', encoding='utf-8') as f:
            f.write(json.dumps(response, default=str))
        print("Wrote response.json")
        break
    except Exception as e:
        print(f"FAILED {m}: {type(e).__name__} - {str(e)[:150]}")
