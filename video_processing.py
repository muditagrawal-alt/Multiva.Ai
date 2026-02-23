import subprocess
import os

def extract_audio(video_path: str) -> str:
    """
    Extract audio from video using ffmpeg and return path to the extracted audio.
    """
    audio_path = os.path.splitext(video_path)[0] + "_audio.wav"
    cmd = [
        "ffmpeg",
        "-y",  # overwrite if exists
        "-i", video_path,
        "-vn",  # no video
        "-acodec", "pcm_s16le",  # WAV format
        "-ar", "16000",  # 16kHz for ASR/voice models
        audio_path
    ]
    subprocess.run(cmd, check=True)
    return audio_path

def merge_audio_with_video(video_path: str, audio_path: str) -> str:
    """
    Merge a new audio file back into the original video.
    """
    output_video_path = os.path.splitext(video_path)[0] + "_output.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",  # copy video stream
        "-map", "0:v:0",  # original video
        "-map", "1:a:0",  # new audio
        "-shortest",  # stop at shortest stream
        output_video_path
    ]
    subprocess.run(cmd, check=True)
    return output_video_path