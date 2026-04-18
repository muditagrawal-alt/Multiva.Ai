import boto3
import os
from dotenv import load_dotenv

# Load .env
load_dotenv()

# Get values from .env
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_BUCKET = os.getenv("R2_BUCKET")

# Create S3 client
s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY
)


def upload_file(file_path, key):
    s3.upload_file(file_path, R2_BUCKET, key)

    # Correct public URL
    return f"{R2_ENDPOINT}/{R2_BUCKET}/{key}"