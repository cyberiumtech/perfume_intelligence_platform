import boto3
import os
from dotenv import load_dotenv

load_dotenv()

client = boto3.client(
    "bedrock",
    region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
)

models = client.list_foundation_models()

print("Available Models:")
for m in models['modelSummaries']:
    print(f"- {m['modelId']}")