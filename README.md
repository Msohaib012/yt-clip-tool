# YT Clip Tool

Search YouTube, read the transcript with timestamps (like YouTube), and export
a clip (specific start → end) or the full video — 720p MP4. Built for the PC.

## Easiest way (Windows, no install)
Download **YT-Clip-Tool.exe** from the latest release and double-click it.
No Python or ffmpeg install needed — everything is bundled. The browser opens
at http://127.0.0.1:5000 automatically.

https://github.com/Msohaib012/yt-clip-tool/releases

## What it does
- **Search** — YouTube-style results (topic, creator, or paste a video link).
- **Transcript** — loads instantly, every line has a timestamp. Click a line to
  jump the video there; the current line highlights and auto-scrolls as the
  video plays (just like YouTube).
- **Clip** — press **S** on a line to set it as start, **E** to set it as end
  (or type the seconds). The range is highlighted in the transcript.
- **Export** — Clip (start → end) or Full video, 720p MP4. Fast mode cuts at
  the nearest keyframe (instant); tick **Precise cut** for frame-accurate
  (slower, re-encodes). The finished file downloads to your computer.

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
- Transcript language follows the video (English videos → English, Hindi → Hindi).
  A video with no captions has no transcript.
- YouTube sometimes rate-limits downloads. If a download fails, wait a bit and
  try again, or try another video.
- Exports land in the `downloads/` folder as well as the browser download.
