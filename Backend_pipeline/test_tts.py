from TTS.api import TTS

tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")

tts.tts_to_file(
    text="This is a test of the XTTS v2 voice synthesis system.",
    speaker_wav="reference.wav",
    language="en",
    file_path="output.wav"
)