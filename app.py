"""
YT Clip Tool — backend.
Search YouTube, fetch transcripts (instant, with timestamps),
export a clip (start-end) or the full video at 720p MP4.

Run:  python app.py   (then open http://127.0.0.1:5000)
As a portable .exe (PyInstaller): double-click, the browser opens itself.
Requires: flask, yt-dlp, youtube-transcript-api, ffmpeg on PATH
(or a bundled ffmpeg.exe next to the exe / inside the bundle).
"""
import os
import re
import sys
import json
import time
import uuid
import threading
import webbrowser
from pathlib import Path

from flask import Flask, request, jsonify, send_file, send_from_directory

import yt_dlp

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except ImportError:
    YouTubeTranscriptApi = None


def _base_path():
    """Dir holding the app + static files (PyInstaller bundle or source tree)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = _base_path() / "static"
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

# 720p MP4: H.264 video (format 136) + m4a audio (format 140).
FORMAT_720P = "136+140"
FORMAT_FALLBACK = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]"

# jobs: job_id -> {status, progress, message, filename, error}
jobs = {}
jobs_lock = threading.Lock()


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    return (name or "video")[:120]


def _ffmpeg_location():
    """Bundled ffmpeg.exe (PyInstaller) if present, else None (system PATH)."""
    if getattr(sys, "frozen", False):
        p = Path(sys._MEIPASS) / "ffmpeg.exe"
        if p.exists():
            return str(p)
    p = BASE_DIR / "ffmpeg.exe"
    if p.exists():
        return str(p)
    return None


def _ydl_base_opts():
    opts = {"quiet": True, "noplaylist": True, "noprogress": True}
    ff = _ffmpeg_location()
    if ff:
        opts["ffmpeg_location"] = ff
    return opts


def _is_ssl_error(exc) -> bool:
    return "CERTIFICATE_VERIFY_FAILED" in str(exc) or "SSL" in str(exc)


def _extract_with_cert_retry(make_ydl, fn):
    """Run fn(ydl); if it dies on an SSL/cert error, retry once with
    nocheckcertificate (VM/proxy case)."""
    try:
        with yt_dlp.YoutubeDL(make_ydl(False)) as ydl:
            return fn(ydl)
    except Exception as e:
        if _is_ssl_error(e):
            with yt_dlp.YoutubeDL(make_ydl(True)) as ydl:
                return fn(ydl)
        raise


# ---------------------------------------------------------------- search
@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "empty query"}), 400

    m = re.search(r"(?:v=|youtu\.be/|/shorts/)([A-Za-z0-9_-]{11})", q)
    try:
        if m:
            vid = m.group(1)

            def _one(ydl):
                return ydl.extract_info(
                    f"https://www.youtube.com/watch?v={vid}", download=False)

            info = _extract_with_cert_retry(
                lambda nocheck: {**_ydl_base_opts(),
                                 "nocheckcertificate": nocheck}, _one)
            if not info:
                return jsonify({"error": "video not found"}), 404
            return jsonify({"results": [{
                "id": info.get("id") or vid,
                "title": info.get("title") or vid,
                "uploader": info.get("uploader") or "",
                "duration": int(info.get("duration") or 0),
                "thumbnail": info.get("thumbnail") or
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            }]})

        def _search(ydl):
            return ydl.extract_info(f"ytsearch10:{q}", download=False)

        info = _extract_with_cert_retry(
            lambda nocheck: {**_ydl_base_opts(), "flat_playlist": True,
                             "extract_flat": True,
                             "nocheckcertificate": nocheck}, _search)
        results = []
        for e in (info or {}).get("entries") or []:
            if not e:
                continue
            vid = e.get("id") or ""
            results.append({
                "id": vid,
                "title": e.get("title") or vid,
                "uploader": e.get("uploader") or "",
                "duration": int(e.get("duration") or 0),
                "thumbnail": e.get("thumbnail") or
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            })
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": "search failed", "detail": str(e)[:300]}), 502


# ------------------------------------------------------------ transcript
@app.route("/api/transcript")
def api_transcript():
    vid = (request.args.get("video_id") or "").strip()
    if not vid:
        return jsonify({"error": "video_id required"}), 400
    if YouTubeTranscriptApi is None:
        return jsonify({"error": "youtube-transcript-api not installed"}), 500
    api = YouTubeTranscriptApi()
    fetched = None
    last_err = None
    for lg in ["en", "hi", "ur"]:
        try:
            fetched = api.fetch(vid, languages=[lg])
            break
        except Exception as e:
            last_err = e
    if fetched is None:
        try:
            tlist = api.list(vid)
            first = next(iter(tlist), None)
            if first is not None:
                fetched = first.fetch()
        except Exception as e:
            last_err = e
    if fetched is None:
        return jsonify({"error": "no transcript for this video",
                        "detail": str(last_err)[:200]}), 404
    segs = fetched.to_raw_data()
    return jsonify({
        "video_id": vid,
        "language": fetched.language,
        "language_code": fetched.language_code,
        "is_generated": fetched.is_generated,
        "segments": [{"start": s["start"], "duration": s["duration"],
                      "text": s["text"]} for s in segs],
    })


# --------------------------------------------------------------- export
def _set_job(job_id, **kw):
    with jobs_lock:
        jobs.setdefault(job_id, {}).update(kw)


def _run_export(job_id, video_id, title, start, end, mode, precise):
    url = f"https://www.youtube.com/watch?v={video_id}"
    safe_title = safe_filename(title or video_id)
    suffix = f"_{int(start)}-{int(end)}s" if mode == "clip" else "_full"
    outtmpl = str(DOWNLOAD_DIR / f"{safe_title}{suffix}.%(ext)s")

    def progress_hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                _set_job(job_id, progress=round(done / total * 100, 1))

    def build_opts(nocheck=False, fmt=FORMAT_720P):
        opts = {**_ydl_base_opts(), "quiet": False, "noprogress": True,
                "format": fmt, "outtmpl": outtmpl,
                "nocheckcertificate": nocheck,
                "progress_hooks": [progress_hook]}
        if mode == "clip":
            opts["download_ranges"] = (
                lambda info, ydl: [{"start_time": float(start),
                                     "end_time": float(end)}])
            if precise:
                opts["force_keyframes_at_cuts"] = True
        return opts

    _set_job(job_id, status="downloading", progress=0, message="Downloading...")
    try:
        try:
            with yt_dlp.YoutubeDL(build_opts(False)) as ydl:
                ydl.download([url])
        except Exception as e:
            if _is_ssl_error(e):
                _set_job(job_id, message="Retrying download...")
                with yt_dlp.YoutubeDL(build_opts(True)) as ydl:
                    ydl.download([url])
            elif "requested format" in str(e).lower() or "format" in str(e).lower():
                with yt_dlp.YoutubeDL(build_opts(False, FORMAT_FALLBACK)) as ydl:
                    ydl.download([url])
            else:
                raise
    except Exception as e:
        _set_job(job_id, status="error",
                 error="Download failed. " + str(e)[-300:])
        return

    produced = sorted(DOWNLOAD_DIR.glob(f"{safe_title}{suffix}.*"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    if not produced:
        _set_job(job_id, status="error", error="Export finished but no file found.")
        return
    f = produced[0]
    _set_job(job_id, status="done", progress=100,
             filename=f.name, size=f.stat().st_size, message="Ready.")


@app.route("/api/export", methods=["POST"])
def api_export():
    data = request.get_json(force=True, silent=True) or {}
    video_id = (data.get("video_id") or "").strip()
    title = (data.get("title") or "").strip()
    mode = (data.get("mode") or "clip").strip()  # clip | full
    if not video_id:
        return jsonify({"error": "video_id required"}), 400
    try:
        start = float(data.get("start") or 0)
        end = float(data.get("end") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "bad start/end"}), 400
    if mode == "clip" and (end <= start):
        return jsonify({"error": "end must be after start"}), 400
    precise = bool(data.get("precise"))
    job_id = uuid.uuid4().hex[:12]
    _set_job(job_id, status="queued", progress=0, message="Starting...",
             video_id=video_id, mode=mode)
    t = threading.Thread(target=_run_export,
                         args=(job_id, video_id, title, start, end, mode, precise),
                         daemon=True)
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/api/export/status/<job_id>")
def api_export_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "unknown job"}), 404
    return jsonify(job)


@app.route("/api/download/<job_id>")
def api_download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or job.get("status") != "done" or not job.get("filename"):
        return jsonify({"error": "file not ready"}), 404
    return send_file(DOWNLOAD_DIR / job["filename"], as_attachment=True)


@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


if __name__ == "__main__":
    print("YT Clip Tool — open http://127.0.0.1:5000")
    # Portable .exe: open the browser automatically.
    if getattr(sys, "frozen", False):
        threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
