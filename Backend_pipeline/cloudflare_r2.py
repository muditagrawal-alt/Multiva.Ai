import os
import boto3
from dotenv import load_dotenv

load_dotenv()

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_BUCKET = os.getenv("R2_BUCKET")
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL")

s3_client = None

if R2_ACCESS_KEY and R2_SECRET_KEY and R2_ENDPOINT:
    s3_client = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        region_name="auto"
    )

def upload_file_to_r2(file_path: str, object_name: str, content_type: str = "video/mp4") -> str:
    if not s3_client:
        return None
    try:
        s3_client.upload_file(
            file_path, 
            R2_BUCKET, 
            object_name,
            ExtraArgs={"ContentType": content_type}
        )
        if R2_PUBLIC_URL.endswith("/"):
            return f"{R2_PUBLIC_URL}{object_name}"
        return f"{R2_PUBLIC_URL}/{object_name}"
    except Exception as e:
        print(f"[R2] Upload failed: {e}")
        return None

def delete_file_from_r2(object_name: str):
    if not s3_client:
        return
    try:
        s3_client.delete_object(Bucket=R2_BUCKET, Key=object_name)
    except Exception as e:
        print(f"[R2] Delete failed: {e}")
