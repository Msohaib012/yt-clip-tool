# YT Clip Tool

Search YouTube, read the transcript with timestamps (like YouTube), and export
a clip (specific start → end) or the full video — 720p MP4. Built for the PC.

## Easiest way (Windows, no install)
Download **YT-Clip-Tool.exe** from the latest release and double-click it.
No Python or ffmpeg install needed — everything is bundled. The browser opens
at http://127.0.0.1:5000 automatically.

https://github.com/Msohaib012/yt-clip-tool/releases

## What's new in v1.2.0
- **New interface** — clean, minimal, premium dark design (plus a light theme).
- **Precision clip editor** — drag the start/end handles on the timeline, press
  **I** / **O** to mark in/out at the current time, ±1s steppers, **Preview clip**.
- **Quality picker** — 360p / 480p / 720p / 1080p / Best, or **audio-only MP3**.
- **Export control** — live speed + ETA, **Cancel**, *Show in folder*, *Open folder*.
- **Settings** — choose your download folder and default quality (saved in `config.json`).
- Keyboard: `/` search · `I` start · `O` end · `Space` play/pause · `←` `→` seek 5s.
- Auto-picks a free port if 5000 is busy.

## What it does (v1.1.0 base)
- **Search** — YouTube-style results (topic, creator, or paste a video link),
  with YouTube-like autocomplete suggestions as you type.
- **Transcript** — loads instantly, one complete sentence per line, each with a
  timestamp. Click a line to jump the video there; the current line highlights
  and auto-scrolls as the video plays — scroll yourself and auto-follow pauses
  (a "Follow current line" button brings it back). **Search words in the
  transcript** — matches highlight and ↑/↓ jumps straight to each matching line.
  Switch language: Original, English, Hindi, Urdu or Roman Urdu. Copy the
  transcript or download it as .txt.
- **Clip** — press **S** on a line to set it as start, **E** to set it as end
  (or type the seconds). The range is highlighted in the transcript.
- **Export** — Clip (start → end) or Full video, 720p MP4. Fast mode cuts at
  the nearest keyframe (the clip can start/end 1–2 seconds off); tick
  **Precise cut (frame-accurate)** to re-encode so the clip starts and ends
  exactly at the seconds you picked (slower). Files save to your computer's
  Downloads folder, and recent exports are listed in the app.

## Run from source (Windows)
1. Install **Python 3.10+** from python.org — tick **"Add Python to PATH"**.
2. Install **ffmpeg**: `winget install Gyan.FFmpeg` (or download from gyan.dev).
3. Unzip this folder anywhere. **Double-click `run.bat`.**
   First run installs the packages (flask, yt-dlp, youtube-transcript-api).
   The browser opens at http://127.0.0.1:5000

Manual run (if run.bat is not used):
```
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python app.py
```

## Notes
- Everything runs on YOUR PC — no account, no server, no upload. Videos and
  clips stay on your computer.
- Transcript language: Original follows the video (English videos → English,
  Hindi → Hindi). English / Hindi / Urdu come free from YouTube's own
  auto-translation. Roman Urdu needs a Gemini API key — add it in Settings
  (gear icon, top right); the key stays on your PC and is only sent to Google
  Gemini when you switch to Roman Urdu. A video with no captions has no transcript.
- YouTube sometimes rate-limits downloads. If a download fails, wait a bit and
  try again, or try another video.
- Exports save to your computer's Downloads folder (Windows: `C:\Users\<you>\Downloads`).
