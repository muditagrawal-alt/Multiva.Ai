import os
import time
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None

def create_video_entry(user_id: str, original_filename: str, original_language: str, target_language: str, duration: str = "0:00") -> dict:
    if not supabase:
        return None
    
    expires_at = int(time.time()) + (2 * 3600)  # 2 hours for uploaded video
    
    data = {
        "user_id": user_id,
        "title": original_filename,
        "original_lang": original_language,
        "target_lang": target_language,
        "duration": duration,
        "status": "uploading",
        "expires_at": expires_at,
        "created_at": int(time.time())
    }
    
    try:
        response = supabase.table("videos").insert(data).execute()
        if response.data:
            return response.data[0]
        return None
    except Exception as e:
        print(f"[Supabase] create_video_entry error: {e}")
        return None

def update_video_status(video_id: int, status: str, original_url: str = None):
    if not supabase: return
    data = {"status": status}
    if original_url:
        data["original_url"] = original_url
    try:
        supabase.table("videos").update(data).eq("id", video_id).execute()
    except Exception as e:
        print(f"[Supabase] update_video_status error: {e}")

def save_final_video(video_id: int, final_url: str) -> str:
    if not supabase: return None
    expires_at = int(time.time()) + (2 * 24 * 3600)  # 2 days for generated video
    data = {
        "dubbed_url": final_url,
        "status": "done",
        "expires_at": expires_at
    }
    try:
        supabase.table("videos").update(data).eq("id", video_id).execute()
        return final_url
    except Exception as e:
        print(f"[Supabase] save_final_video error: {e}")
        return None

def mark_video_failed(video_id: int):
    if not supabase: return
    try:
        supabase.table("videos").update({"status": "failed"}).eq("id", video_id).execute()
    except Exception as e:
        pass

def get_user_videos(user_id: str):
    if not supabase: return []
    try:
        response = supabase.table("videos").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
        return response.data
    except Exception as e:
        # Returning [] here made an outage indistinguishable from "this
        # user has no videos", so the UI cheerfully reported an empty
        # library while the database was down. The caller decides how to
        # present the failure; it cannot do that if it never sees one.
        print(f"[Supabase] get_user_videos error: {e}")
        raise

def delete_video_entry(video_id: int):
    if not supabase: return
    try:
        supabase.table("videos").delete().eq("id", video_id).execute()
    except Exception as e:
        print(f"[Supabase] delete_video_entry error: {e}")

def get_expired_videos():
    if not supabase: return []
    try:
        current_time = int(time.time())
        response = supabase.table("videos").select("*").lt("expires_at", current_time).execute()
        return response.data
    except Exception as e:
        print(f"[Supabase] get_expired_videos error: {e}")
        return []
