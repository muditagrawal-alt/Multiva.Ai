import uuid
from datetime import datetime, timedelta

# Import your services
try:
    from Database.storage.r2_service import upload_file
    from Database.db.supabase_service import supabase
    SUPABASE_AVAILABLE = True
except Exception as e:
    SUPABASE_AVAILABLE = False
    supabase = None
    print(f"[DataManager] Supabase/R2 not available: {e}")


# ------------------ CREATE VIDEO ENTRY ------------------

def create_video_entry(user_id: str, title: str, original_language: str = None,
                       target_language: str = None, duration: str = None):
    """
    Create a new video record in the database.
    Returns dict with 'id' key on success, None on failure.
    """
    if not SUPABASE_AVAILABLE:
        print("[DataManager] DB unavailable, skipping create_video_entry")
        return None

    video_id = str(uuid.uuid4())

    data = {
        "video_id": video_id,
        "user_id": user_id,
        "title": title,
        "original_language": original_language,
        "target_language": target_language,
        "duration": duration,
        "processing_status": "uploaded",
        "created_at": datetime.now().isoformat(),
    }

    try:
        response = supabase.table("videos").insert(data).execute()
        # Return a dict with 'id' matching what app.py expects
        return {"id": video_id, "video_id": video_id}
    except Exception as e:
        print(f"[DataManager] Error creating video entry: {e}")
        return None


# ------------------ UPDATE STATUS ------------------

def update_video_status(video_id: str, status: str, **kwargs):
    """
    Update processing status and optionally set other fields.
    Supported kwargs: original_url, dubbed_url, error_message
    """
    if not SUPABASE_AVAILABLE:
        return

    update_data = {"processing_status": status}

    # Allow caller to pass additional fields
    allowed_fields = {"original_url", "dubbed_url", "error_message"}
    for key, value in kwargs.items():
        if key in allowed_fields and value is not None:
            update_data[key] = value

    try:
        supabase.table("videos").update(update_data).eq("video_id", video_id).execute()
    except Exception as e:
        print(f"[DataManager] Error updating status for {video_id}: {e}")


# ------------------ SAVE FINAL VIDEO ------------------

def save_final_video(video_id: str, final_url: str):
    """
    Mark video as completed and store the dubbed video URL.
    """
    if not SUPABASE_AVAILABLE:
        return None

    try:
        supabase.table("videos").update({
            "dubbed_url": final_url,
            "processing_status": "done",
        }).eq("video_id", video_id).execute()
        return final_url
    except Exception as e:
        print(f"[DataManager] Error saving final video for {video_id}: {e}")
        return None


# ------------------ FAIL CASE ------------------

def mark_video_failed(video_id: str, error_message: str = None):
    """
    Mark video as failed.
    """
    if not SUPABASE_AVAILABLE:
        return

    update_data = {"processing_status": "failed"}
    if error_message:
        update_data["error_message"] = error_message

    try:
        supabase.table("videos").update(update_data).eq("video_id", video_id).execute()
    except Exception as e:
        print(f"[DataManager] Error marking video failed {video_id}: {e}")


# ------------------ GET USER VIDEOS ------------------

def get_user_videos(user_id: str):
    """
    Fetch all videos for a given user.
    Returns a list of dicts.
    """
    if not SUPABASE_AVAILABLE:
        return []

    try:
        response = (
            supabase.table("videos")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return response.data or []
    except Exception as e:
        print(f"[DataManager] Error fetching videos for {user_id}: {e}")
        return []


# ------------------ GET EXPIRED VIDEOS ------------------

def get_expired_videos(expiry_hours: int = 72):
    """
    Fetch videos older than `expiry_hours` that are completed or failed,
    so they can be cleaned up from storage.
    """
    if not SUPABASE_AVAILABLE:
        return []

    cutoff = (datetime.now() - timedelta(hours=expiry_hours)).isoformat()

    try:
        response = (
            supabase.table("videos")
            .select("*")
            .lt("created_at", cutoff)
            .in_("processing_status", ["done", "failed"])
            .execute()
        )
        return response.data or []
    except Exception as e:
        print(f"[DataManager] Error fetching expired videos: {e}")
        return []


# ------------------ DELETE VIDEO ENTRY ------------------

def delete_video_entry(video_id):
    """
    Delete a video record by its video_id (string UUID) or integer id.
    """
    if not SUPABASE_AVAILABLE:
        return

    try:
        # Support both UUID string and integer id
        if isinstance(video_id, int):
            supabase.table("videos").delete().eq("id", video_id).execute()
        else:
            supabase.table("videos").delete().eq("video_id", video_id).execute()
    except Exception as e:
        print(f"[DataManager] Error deleting video {video_id}: {e}")


# ------------------ GET SINGLE VIDEO ------------------

def get_video(video_id: str):
    """
    Fetch video details by video_id.
    """
    if not SUPABASE_AVAILABLE:
        return None

    try:
        response = supabase.table("videos").select("*").eq("video_id", video_id).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        print(f"[DataManager] Error fetching video {video_id}: {e}")
        return None