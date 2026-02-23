from fastapi import FastAPI, File, UploadFile, Query
from fastapi.responses import FileResponse
import os
import shutil

from video_processing import extract_audio, merge_audio_with_video
from speech_to_text import transcribe_audio
from translation import translate_text
from tts_module import synthesize_voice

app = FastAPI(title="Multilingual Voice Cloning API")

UPLOAD_DIR = "temp_uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)


@app.post("/process_video/")
async def process_video(
    file: UploadFile = File(...),
    target_language: str = Query(..., description="Target language code")
):

    input_path = os.path.join(UPLOAD_DIR, file.filename)

    with open(input_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # 1️⃣ Extract audio
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

    # 4️⃣ Voice cloning
    synthesized_audio = synthesize_voice(
        translated_text,
        language=target_language,
        speaker_wav=audio_path
    )

    # 5️⃣ Merge back
    output_video_path = merge_audio_with_video(
        input_path,
        synthesized_audio
    )

    return FileResponse(
        output_video_path,
        media_type="video/mp4",
        filename="output.mp4"
    )