from TTS.api import TTS

# Load the medium XTTS model (it will download fresh on the GPU machine)
tts = TTS(model_name="tts_models/multilingual/multi-dataset/xtts_v2")

# Some text to speak
text = "Hello, this is a test of voice cloning."

# Use your speaker audio
speaker_wav = "speaker.wav"

# Output file
output_file = "cloned_test.wav"

# Generate voice-cloned audio
tts.tts_to_file(
    text=text,
    speaker_wav=speaker_wav,
    language="en",  # original language of speaker audio
    file_path=output_file
)

print("Done! Check cloned_test.wav for the output.")