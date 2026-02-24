from fastapi import FastAPI, File, UploadFile, Query
from fastapi.responses import FileResponse
import os
import shutil
from pathlib import Path

from video_processing import extract_audio
from speech_to_text import transcribe_audio
from translation import translate_text
from tts_module import synthesize_voice

# Import lip-sync generator
from lip_sync_generate import generate_lip_synced_video

app = FastAPI(title="Multilingual Voice Cloning API")

UPLOAD_DIR = "temp_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.post("/process_video/")
async def process_video(
    file: UploadFile = File(...),
    target_language: str = Query(..., description="Target language code")
):

    input_path = os.path.join(UPLOAD_DIR, file.filename)

    # Save uploaded video
    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 1️⃣ Extract audio from original video
    audio_path = extract_audio(input_path)

    # 2️⃣ Transcribe
    result = transcribe_audio(audio_path)
    original_text = result["text"]
    source_language = result["language"]

    # 3️⃣ Translate
    translated_text = translate_text(
        original_text,
        source_language,
        target_language
    )

    # 4️⃣ Voice cloning / TTS
    synthesized_audio_path = synthesize_voice(
        translated_text,
        language=target_language,
        speaker_wav=audio_path
    )

    # 5️⃣ Lip-sync video with synthesized audio
    output_video_path = Path(UPLOAD_DIR) / f"output_{file.filename}"
    generate_lip_synced_video(
        input_video_path=input_path,
        audio_path=synthesized_audio_path,
        output_path=str(output_video_path)
    )

    # 6️⃣ Return final lip-synced video
    return FileResponse(
        output_video_path,
        media_type="video/mp4",
        filename="output.mp4"
    )