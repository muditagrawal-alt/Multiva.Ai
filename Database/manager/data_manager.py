import uuid
from datetime import datetime

# Import your services
from Database.storage.r2_service import upload_file
from Database.db.supabase_service import supabase


# ------------------ CREATE VIDEO ENTRY ------------------

def create_video_entry(user_id, file_path):
    """
    Upload input video to R2 and create DB entry
    """

    video_id = str(uuid.uuid4())
    key = f"uploads/{video_id}.mp4"

    # Upload to R2
    url = upload_file(file_path, key)

    if not url:
        return None

    # Insert into DB
    data = {
        "video_id": video_id,
        "user_id": user_id,
        "original_video_url": url,
        "processing_status": "uploaded",
        "created_at": datetime.now().isoformat()
    }

    supabase.table("videos").insert(data).execute()

    return {
        "video_id": video_id,
        "video_url": url
    }


# ------------------ UPDATE STATUS ------------------

def update_video_status(video_id, status):
    """
    Update processing status
    """
    supabase.table("videos").update({
        "processing_status": status
    }).eq("video_id", video_id).execute()


# ------------------ SAVE FINAL VIDEO ------------------

def save_final_video(video_id, file_path):
    """
    Upload processed video and update DB
    """

    key = f"outputs/{video_id}_final.mp4"

    # Upload output to R2
    url = upload_file(file_path, key)

    if not url:
        return None

    # Update DB
    supabase.table("videos").update({
        "final_video_url": url,
        "processing_status": "completed"
    }).eq("video_id", video_id).execute()

    return url


# ------------------ FAIL CASE ------------------

def mark_video_failed(video_id, error_message=None):
    """
    Mark video as failed
    """
    supabase.table("videos").update({
        "processing_status": "failed"
    }).eq("video_id", video_id).execute()


# ------------------ GET VIDEO ------------------

def get_video(video_id):
    """
    Fetch video details
    """
    response = supabase.table("videos").select("*").eq("video_id", video_id).execute()
    return response.data