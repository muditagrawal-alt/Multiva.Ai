# 🎬 Multiva AI — Auto Dubbing Platform

Multiva AI is an end-to-end **AI-powered video dubbing platform** that automatically translates and dubs videos into multiple languages while preserving the original speaker’s voice.

---

## 🚀 Features

* 🎤 Speech-to-Text using Whisper
* 🌍 Multilingual Translation (NLLB)
* 🗣️ Voice Cloning (Coqui XTTS)
* 🎥 Lip-syncing (MuseTalk)
* ☁️ Scalable Storage using Cloudflare R2
* ⚡ Portable AI Execution using Run Anywhere SDK

---

## 🧠 System Architecture

```text
User Upload (Frontend)
        ↓
Cloudflare R2 (Input Storage)
        ↓
Run Anywhere Backend (AI Pipeline)
        ↓
Whisper → Translation → TTS → LipSync
        ↓
Cloudflare R2 (Output Storage)
        ↓
Frontend Playback / Download
```

---

## 🧩 Tech Stack

### 🖥️ Frontend

* HTML / CSS / JavaScript
* (Optional: React / Next.js)

### ⚙️ Backend

* Python / FastAPI OR Node.js
* Run Anywhere SDK (Portable Execution Layer)

### 🤖 AI Models

* Whisper (Speech-to-Text)
* Facebook NLLB (Translation)
* Coqui XTTS (Voice Cloning)
* Wav2Lip (Lip Sync)

### ☁️ Storage

* Cloudflare R2 (Object Storage)

### 🗄️ Database (Optional)

* Supabase (PostgreSQL + Auth)

---

## ⚡ What is Run Anywhere SDK?

Run Anywhere SDK enables the AI pipeline to run consistently across:

* 💻 Local machines
* 🖥️ Teammates' systems
* ☁️ Cloud environments

It ensures:

* Dependency management
* Environment consistency
* Easy deployment

> Note: It does NOT affect model accuracy or speed — it ensures portability.

---

## 📂 Project Structure

```bash
multiva-ai/
│
├── frontend/
│   ├── index.html
│   ├── Login.html
│   ├── app.html
│   ├── lang.json
│   ├── package-lock.json
│   ├── style.css
│   └── scripts.js
│   
├── backend_pipeline
│   ├── __init__.py
│   ├── app.py
│   ├── Demo.py
│   ├── lip_sync_generate.py
│   ├── lip_sync_loader.py
│   ├── lip_sync_test.py
│   ├── speech_to_text.py
│   ├── test_tts.py
│   ├── translation.py
│   ├── tts_module.py
│   ├── video_processing.py
│   └── xtts_test.py
└── README.md
```

---

## 🔐 Environment Variables

Create a `.env` file:

```env
R2_ACCESS_KEY=your_access_key
R2_SECRET_KEY=your_secret_key
R2_BUCKET=multiva-storage
R2_ENDPOINT=https://<account-id>.r2.cloudflarestorage.com
```

---

## ☁️ Cloudflare R2 Setup

1. Create a bucket: `multiva-storage`
2. Generate API Token (Object Read & Write)
3. Store videos using structured keys:

```bash
inputs/video.mp4
outputs/dubbed.mp4
audio/extracted.wav
```

---

## 📦 Installation

### 1. Clone Repo

```bash
git clone https://github.com/your-username/multiva-ai.git
cd multiva-ai
```

### 2. Install Dependencies

#### Python

```bash
pip install -r requirements.txt
```


## ▶️ Run the Project

### Backend

```bash
python main.py
# or
node server.js
```

### Frontend

Open:

```bash
http://127.0.0.1:5500/Login.html
```

---

## 🔄 Pipeline Flow

1. Upload video
2. Store in R2
3. Extract audio
4. Transcribe using Whisper
5. Translate text
6. Generate cloned voice
7. Lip-sync video
8. Store output in R2

---

## 📈 Future Improvements

* Real-time dubbing
* Speaker emotion preservation
* WebRTC streaming
* GPU cloud deployment

---

## 🤝 Contributors

* Mudit — AI Pipeline & Models
* Aditya — Frontend & Integration
* Team Multiva

---

## 📄 License

MIT License

---

## 💡 Final Note

Multiva achieves ~85% quality using optimized medium-sized models, making it efficient and scalable compared to large, resource-heavy solutions.

---
