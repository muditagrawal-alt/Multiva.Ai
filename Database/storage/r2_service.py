import boto3
import os
from dotenv import load_dotenv
from botocore.exceptions import NoCredentialsError, ClientError

# Load environment variables
load_dotenv()

# ------------------ ENV VARIABLES ------------------

R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_BUCKET = os.getenv("R2_BUCKET")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL")

# ------------------ VALIDATION ------------------

if not all([R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET, R2_PUBLIC_URL]):
    raise ValueError("❌ Missing one or more R2 environment variables")

# ------------------ S3 CLIENT ------------------

s3 = boto3.client(
    "s3",
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY
)

# ------------------ UPLOAD ------------------

def upload_file(file_path, key):
    """
    Upload file to R2 and return public URL

    Args:
        file_path (str): local file path
        key (str): path in bucket (e.g. uploads/video.mp4)

    Returns:
        str: public URL
    """
    try:
        s3.upload_file(file_path, R2_BUCKET, key)
        return f"{R2_PUBLIC_URL}/{key}"

    except FileNotFoundError:
        print("❌ File not found")
        return None

    except NoCredentialsError:
        print("❌ Invalid R2 credentials")
        return None

    except ClientError as e:
        print(f"❌ Upload failed: {e}")
        return None


# ------------------ DELETE ------------------

def delete_file(key):
    """
    Delete file from R2
    """
    try:
        s3.delete_object(Bucket=R2_BUCKET, Key=key)
        return True

    except ClientError as e:
        print(f"❌ Delete failed: {e}")
        return False


# ------------------ GET URL ------------------

def get_file_url(key):
    """
    Generate public URL from key
    """
    return f"{R2_PUBLIC_URL}/{key}"