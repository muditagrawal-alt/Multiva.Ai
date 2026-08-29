import os
from dotenv import load_dotenv
from supabase import create_client

# Load environment variables
load_dotenv()

# Get values from .env
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Create client
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ------------------ VIDEO ------------------

def insert_video(data):
    try:
        response = supabase.table("videos").insert(data).execute()
        return response
    except Exception as e:
        print("Error inserting video:", e)
        return None


def get_video(video_id):
    try:
        response = supabase.table("videos").select("*").eq("id", video_id).execute()
        return response
    except Exception as e:
        print("Error fetching video:", e)
        return None


# ------------------ JOB ------------------

def create_job(data):
    try:
        response = supabase.table("jobs").insert(data).execute()
        return response
    except Exception as e:
        print("Error creating job:", e)
        return None


def update_job(job_id, data):
    try:
        response = supabase.table("jobs").update(data).eq("id", job_id).execute()
        return response
    except Exception as e:
        print("Error updating job:", e)
        return None


def get_job(job_id):
    try:
        response = supabase.table("jobs").select("*").eq("id", job_id).execute()
        return response
    except Exception as e:
        print("Error fetching job:", e)
        return None