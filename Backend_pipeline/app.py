from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, File, UploadFile, Query, HTTPException
from fastapi.responses import FileResponse
import os
import json
import shutil
import subprocess
from pathlib import Path

from video_processing import extract_audio
from speech_to_text import transcribe_audio
from translation import translate_text
from tts_module import synthesize_voice

app = FastAPI(title="Multilingual Voice Cloning API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "temp_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def get_duration(path: str) -> float:
    """Return duration in seconds using ffprobe."""
    result = subprocess.run([
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", path
    ], capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    for stream in data["streams"]:
        if "duration" in stream:
            return float(stream["duration"])
    raise ValueError(f"Could not read duration from {path}")


def stretch_audio_to_duration(audio_path: str, target_duration: float) -> str:
    """
    Time-stretch TTS audio to exactly match video length using ffmpeg atempo.
    Chains multiple atempo filters if ratio is outside the 0.5–2.0 range.
    """
    audio_duration = get_duration(audio_path)
    ratio = audio_duration / target_duration

    print(f"[SYNC] TTS={audio_duration:.2f}s | Video={target_duration:.2f}s | ratio={ratio:.3f}")

    if abs(ratio - 1.0) < 0.02:
        print("[SYNC] Within 2% — skipping stretch.")
        return audio_path

    filters = []
    r = ratio
    while r > 2.0:
        filters.append("atempo=2.0")
        r /= 2.0
    while r < 0.5:
        filters.append("atempo=0.5")
        r /= 0.5
    filters.append(f"atempo={r:.6f}")
    atempo_filter = ",".join(filters)

    stretched_path = audio_path.replace(".wav", "_stretched.wav")
    subprocess.run([
        "ffmpeg", "-y",
        "-i", audio_path,
        "-filter:a", atempo_filter,
        stretched_path
    ], check=True)

    print(f"[SYNC] Stretched audio → {stretched_path} ({get_duration(stretched_path):.2f}s)")
    return stretched_path


@app.post("/process_video/")
async def process_video(
    file: UploadFile = File(...),
    target_language: str = Query(..., description="Target language code")
):
    input_path = os.path.join(UPLOAD_DIR, file.filename)

    # 1️⃣ Save uploaded video
    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    if not os.path.exists(input_path) or os.path.getsize(input_path) < 1000:
        raise HTTPException(status_code=400, detail="Uploaded file is missing or too small")

    print(f"[APP] Input video: {input_path} ({os.path.getsize(input_path)} bytes)")

    # 2️⃣ Extract audio
    audio_path = extract_audio(input_path)
    print(f"[APP] Extracted audio: {audio_path}")

    # 3️⃣ Transcribe
    result = transcribe_audio(audio_path)
    original_text = result["text"]
    source_language = result["language"]
    print(f"[APP] Transcribed ({source_language}): {original_text[:80]}...")

    # 4️⃣ Translate
    translated_text = translate_text(original_text, source_language, target_language)
    print(f"[APP] Translated ({target_language}): {translated_text[:80]}...")

    # 5️⃣ TTS
    synthesized_audio_path = synthesize_voice(
        translated_text,
        language=target_language,
        speaker_wav=audio_path
    )

    # ── Validate TTS output before doing anything with it ──
    if not os.path.exists(synthesized_audio_path):
        raise HTTPException(status_code=500, detail="TTS synthesis produced no output file")
    tts_size = os.path.getsize(synthesized_audio_path)
    if tts_size < 1000:
        raise HTTPException(status_code=500, detail=f"TTS output suspiciously small: {tts_size} bytes")
    print(f"[APP] TTS audio: {synthesized_audio_path} ({tts_size} bytes, {get_duration(synthesized_audio_path):.2f}s)")

    # 6️⃣ Stretch TTS audio to match video duration
    video_duration = get_duration(input_path)
    synthesized_audio_path = stretch_audio_to_duration(synthesized_audio_path, video_duration)

    # 7️⃣ Merge: replace original audio with TTS audio
    output_video_path = Path(UPLOAD_DIR) / f"output_{file.filename}"

    # -map 0:v:0  → video from input
    # -map 1:a:0  → audio from TTS (NOT from original video)
    # -c:v copy   → no video re-encode (fast)
    # -c:a aac    → encode TTS wav to aac
    # NOTE: no -shortest here — audio is already stretched to match
    subprocess.run([
        "ffmpeg", "-y",
        "-i", input_path,
        "-i", synthesized_audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_video_path)
    ], check=True)

    # ── Validate output ──
    if not os.path.exists(output_video_path):
        raise HTTPException(status_code=500, detail="ffmpeg merge produced no output file")
    out_size = os.path.getsize(output_video_path)
    print(f"[APP] Output video: {output_video_path} ({out_size} bytes)")

    return FileResponse(
        output_video_path,
        media_type="video/mp4",
        filename="output.mp4"
    )
