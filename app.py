"""
YT Clip Tool v1.2.0 — backend.
Search YouTube, fetch transcripts (instant, with timestamps),
export a clip (start-end) or the full video at 720p MP4.

Run:  python app.py   (then open http://127.0.0.1:5000)
As a portable .exe (PyInstaller): double-click, the browser opens itself.
Requires: flask, yt-dlp, youtube-transcript-api, ffmpeg on PATH
(or a bundled ffmpeg.exe next to the exe / inside the bundle).

v1.2.0:
- Quality picker (360/480/720/1080/Best) + Audio-only MP3 export
- Cancel export, live speed/ETA, open file / open folder from the app
- Settings saved on disk (download folder, default quality) in config.json
- Auto-picks a free port if 5000 is busy

v1.1.0:
- Search suggestions: /api/suggest (Google Suggest proxy, no key)
- Transcript languages: Original / English / Hindi / Urdu / Roman Urdu
  (YouTube auto-translate for en/hi/ur via yt-dlp; Gemini for Roman Urdu)
- Transcript lines merged to one complete sentence per timestamp (min 10s)
- Exports go to the OS default Downloads folder
- Transcript cache in transcripts/ (per video_id + language)
- Recent exports list: /api/recent
"""
import json
import os
import re
import shutil
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
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


def _writable_base():
    """Writable dir next to the app (the .exe's folder when frozen,
    the source dir when running from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return BASE_DIR


def _default_download_dir():
    """The OS default Downloads folder (Desktop's Downloads on Windows).
    Falls back to the app's own downloads/ folder if it does not exist."""
    home_dl = Path.home() / "Downloads"
    if home_dl.is_dir():
        return home_dl
    return BASE_DIR / "downloads"


CONFIG_PATH = _writable_base() / "config.json"
APP_VERSION = "1.2.3"


def _load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_config(cfg):
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def _resolve_download_dir():
    custom = (_load_config().get("download_dir") or "").strip()
    if custom:
        try:
            p = Path(custom).expanduser()
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            pass
    return _default_download_dir()


DOWNLOAD_DIR = _resolve_download_dir()
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS_DIR = _writable_base() / "transcripts"
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")

# 720p MP4: H.264 video (format 136) + m4a audio (format 140).
FORMAT_720P = "136+140"
FORMAT_FALLBACK = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]"
QUALITIES = ("360", "480", "720", "1080", "best", "audio")


def _format_for(quality):
    """yt-dlp format selector for a quality choice. MP4 + H.264 preferred
    (v1.1.0 pinned format 136 for the same reason: AV1 also ships in MP4
    containers, and AV1 is heavier to edit in DaVinci Resolve)."""
    if quality == "audio":
        return "bestaudio[ext=m4a]/bestaudio/best"
    if quality == "best":
        return ("bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/"
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best")
    h = int(quality)
    return (f"bestvideo[height<={h}][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best")


class _Cancelled(Exception):
    """Raised inside a progress hook to abort a running export."""

# jobs: job_id -> {status, progress, message, filename, error}
jobs = {}
jobs_lock = threading.Lock()

LANG_LABELS = {"original": "Original", "en": "English", "hi": "Hindi",
               "ur": "Urdu", "roman": "Roman Urdu"}


class _KeyRequired(Exception):
    """Raised when Roman Urdu is requested but no Gemini key is set."""


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    return (name or "video")[:120]


def _ffmpeg_dir():
    """Directory holding ffmpeg.exe/ffprobe.exe, or None.
    Checked in order (first hit wins):
      1. next to the running .exe (portable ZIP distribution)
      2. inside the PyInstaller bundle (sys._MEIPASS)
      3. next to app.py (source run)
      4. system PATH (shutil.which)
    Returns the DIRECTORY so yt-dlp finds both ffmpeg and ffprobe in it.
    Also prepends it to PATH so any subprocess finds it too.
    """
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent)
        candidates.append(Path(sys._MEIPASS))
    candidates.append(BASE_DIR)
    for d in candidates:
        if (d / "ffmpeg.exe").exists() or (d / "ffmpeg").exists():
            try:
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            except Exception:
                pass
            return str(d)
    if shutil.which("ffmpeg"):
        return None  # on PATH already; yt-dlp will find it
    return None


def _ffmpeg_location():
    return _ffmpeg_dir()


def _ydl_base_opts():
    opts = {"quiet": True, "noplaylist": True, "noprogress": True}
    ff = _ffmpeg_location()
    if ff:
        opts["ffmpeg_location"] = ff
    return opts


def _is_ssl_error(exc) -> bool:
    return "CERTIFICATE_VERIFY_FAILED" in str(exc) or "SSL" in str(exc)


def _is_retryable_http(exc) -> bool:
    """Transient server-side failures worth retrying: rate limit (429)
    and temporary unavailability (500/502/503), plus hung calls
    (TimeoutError / read timeouts — a degraded Gemini hangs rather
    than failing fast). YouTube and Gemini both return these in waves;
    a single failure used to kill the whole panel."""
    s = str(exc)
    if any(code in s for code in ("429", "500", "502", "503")):
        return True
    low = s.lower()
    return "timed out" in low or "timeout" in low


def _api_try(fn):
    """One call with a single retry (5s) on transient server errors."""
    try:
        return fn()
    except Exception as e:
        if _is_retryable_http(e):
            time.sleep(5)
            return fn()
        raise


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


def _http_bytes(req_or_url, timeout=10):
    """urlopen with one retry on SSL/cert errors (proxy MITM case).
    Accepts a URL string or a prepared urllib Request. Returns raw bytes."""
    if isinstance(req_or_url, str):
        req = urllib.request.Request(req_or_url, headers={"User-Agent": "Mozilla/5.0"})
    else:
        req = req_or_url
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:
        if _is_ssl_error(e):
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
                return r.read()
        raise


# ---------------------------------------------------------------- suggest
_suggest_cache = {}
_SUGGEST_TTL = 600  # seconds


@app.route("/api/suggest")
def api_suggest():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    now = time.time()
    hit = _suggest_cache.get(q)
    if hit and now - hit[0] < _SUGGEST_TTL:
        return jsonify(hit[1])
    try:
        url = ("https://suggestqueries.google.com/complete/search"
               "?client=firefox&ds=yt&hl=en&gl=pk&q=" + urllib.parse.quote(q))
        data = json.loads(_http_bytes(url, timeout=5).decode("utf-8", "replace"))
        items = [str(s) for s in (data[1] if len(data) > 1 else [])][:10]
    except Exception:
        items = []  # fail silently — the search box keeps working
    if len(_suggest_cache) > 200:
        _suggest_cache.clear()
    _suggest_cache[q] = (now, items)
    return jsonify(items)


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
                "views": info.get("view_count"),
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
                "views": e.get("view_count"),
                "thumbnail": e.get("thumbnail") or
                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
            })
        return jsonify({"results": results})
    except Exception as e:
        return jsonify({"error": "search failed", "detail": str(e)[:300]}), 502


# ------------------------------------------------------------ transcript
# --- VTT parsing -------------------------------------------------------
_VTT_TIME = re.compile(r"(\d+):(\d+):(\d+)[.,](\d+)")


def _vtt_ts(s):
    m = _VTT_TIME.match(s.strip())
    if not m:
        return None
    h, mi, sec, ms = (int(x) for x in m.groups())
    return h * 3600 + mi * 60 + sec + ms / 1000.0


def _clean_cue_text(t):
    t = re.sub(r"<[^>]+>", "", t)  # <c>, <00:00:01.500> inline tags
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


def _parse_vtt(text):
    cues = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    for b in blocks:
        lines = [l for l in b.split("\n") if l.strip()]
        if not lines:
            continue
        tline = next((l for l in lines if "-->" in l), None)
        if not tline:
            continue
        a, _, c = tline.partition("-->")
        start = _vtt_ts(a)
        end = _vtt_ts(c.split()[0])
        if start is None or end is None:
            continue
        txt = _clean_cue_text(" ".join(
            l for l in lines if "-->" not in l and not l.strip().isdigit()))
        if txt:
            cues.append({"start": start,
                         "duration": max(0.0, end - start), "text": txt})
    cues.sort(key=lambda s: s["start"])
    return cues


# --- sentence merging --------------------------------------------------
# A word ends a sentence on . ! ? or the Hindi danda । (not on decimals).
_SENT_END = re.compile(r"[.!?\u0964]$")


def _is_sentence_end(word):
    if not _SENT_END.search(word):
        return False
    if re.search(r"\d[.]$", word):  # "5." is a number, not a full stop
        return False
    return True


def _words_with_time(segments):
    """Flatten segments into [word, start, end], dropping the rolling-window
    repeats YouTube auto-captions emit (each cue repeats the tail of the last
    cue, plus 0.01s echo cues). Timings are interpolated across each cue."""
    words = []
    for seg in segments:
        ws = seg["text"].split()
        if not ws:
            continue
        dur = max(float(seg["duration"]), 0.01)
        overlap = 0
        for n in range(min(len(ws), len(words), 14), 0, -1):
            if [w[0] for w in words[-n:]] == ws[:n]:
                overlap = n
                break
        fresh = ws[overlap:]
        for i, w in enumerate(fresh):
            f0 = (overlap + i) / len(ws)
            f1 = (overlap + i + 1) / len(ws)
            words.append([w, seg["start"] + dur * f0, seg["start"] + dur * f1])
    return words


def _merge_sentences(segments, min_s=10.0, max_s=30.0):
    """One complete sentence per line: accumulate sentences until the line
    is >= 10s, then cut at the next sentence boundary (so every line ends
    with a complete sentence). If a sentence never completes, cut at 10s.
    Sentences longer than ~30s split at a comma (or force-split at 30s)."""
    words = _words_with_time(segments)
    if not words:
        return []
    lines = []
    buf = []
    buf_start = None
    last_end_t = None  # end time of the last sentence-ending word in buf

    def flush(chunk):
        if not chunk:
            return
        lines.append({
            "start": round(chunk[0][1], 3),
            "duration": round(max(0.1, chunk[-1][2] - chunk[0][1]), 3),
            "text": " ".join(w[0] for w in chunk),
        })

    for w in words:
        if not buf:
            buf_start = w[1]
            last_end_t = w[1]
        buf.append(w)
        if _is_sentence_end(w[0]):
            last_end_t = w[2]
            if w[2] - buf_start >= min_s:
                flush(buf)
                buf = []
            continue
        span = w[2] - buf_start
        if span >= max_s:
            cut = None
            for i in range(len(buf) - 1, -1, -1):
                if buf[i][0].endswith(",") and buf[i][2] - buf_start >= min_s:
                    cut = i + 1
                    break
            if cut is None:
                cut = len(buf)
            flush(buf[:cut])
            buf = buf[cut:]
            if buf:
                buf_start = buf[0][1]
                last_end_t = buf_start
                for bw in buf:
                    if _is_sentence_end(bw[0]):
                        last_end_t = bw[2]
            continue
        if w[2] - last_end_t >= min_s:
            # The current sentence has run 10s+ without completing — cut here.
            flush(buf)
            buf = []
    if buf:
        flush(buf)
    return lines


# --- subtitle fetching -------------------------------------------------
def _source_lang(vid):
    """Detect the video's original caption language ('hi', 'en', ...)."""
    def _info(ydl):
        return ydl.extract_info(
            f"https://www.youtube.com/watch?v={vid}", download=False)
    info = None
    for attempt in range(2):
        try:
            info = _extract_with_cert_retry(
                lambda nocheck: {**_ydl_base_opts(), "nocheckcertificate": nocheck},
                _info)
            break
        except Exception as e:
            if _is_retryable_http(e) and attempt == 0:
                time.sleep(5)
                continue
            return None
    caps = (info or {}).get("automatic_captions") or {}
    for code in caps:
        if code.endswith("-orig"):
            return code[:-5]
    if caps:
        return sorted(caps.keys())[0].split("-")[0]
    return (info or {}).get("language")


def _download_subs_vtt(vid, lang, native=False):
    """Download one auto-sub track (original or auto-translated) as VTT.
    Returns raw segments. Retries up to 3 times on transient YouTube
    errors (HTTP 429/500/502/503) with backoff — a single 503 used to
    kill the whole transcript panel.
    With native=True (the video's own original track), request
    ``{lang}-orig`` first: since Sep 2026 YouTube resolves a bare code
    like ``en`` to the auto-translated track, which it rate-limits with
    HTTP 429 (yt-dlp#13831). Falls back to the bare code when no -orig
    track exists (e.g. manual captions). Translated tracks are never
    requested with -orig — they are translated with Gemini instead."""
    url = f"https://www.youtube.com/watch?v={vid}"
    tmp = Path(tempfile.mkdtemp(prefix="ytclip_subs_"))
    try:
        def run(nocheck, sublang):
            opts = {**_ydl_base_opts(), "quiet": True, "noplaylist": True,
                    "noprogress": True, "skip_download": True,
                    "writeautomaticsub": True, "sublangs": [sublang],
                    "subformat": "vtt", "outtmpl": str(tmp / "%(id)s"),
                    "sleep_subtitles": 5,
                    "nocheckcertificate": nocheck}
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

        def fetch_one(sublang):
            last_err = None
            for attempt in range(3):
                if attempt:
                    time.sleep(5 if attempt == 1 else 15)
                try:
                    run(False, sublang)
                    break
                except Exception as e:
                    last_err = e
                    if _is_ssl_error(e):
                        try:
                            run(True, sublang)
                            break
                        except Exception as e2:
                            last_err = e2
                    if not _is_retryable_http(last_err):
                        raise last_err
            else:
                raise last_err
            vtts = sorted(tmp.glob("*.vtt"))
            if not vtts:
                raise RuntimeError("no subtitle file produced")
            pick = next((p for p in vtts if f".{sublang}." in p.name),
                        vtts[0])
            return _parse_vtt(
                pick.read_text(encoding="utf-8", errors="replace"))

        if native:
            try:
                segs = fetch_one(f"{lang}-orig")
                if segs:
                    return segs
            except Exception as e:
                # No native track (e.g. manual captions only): try the bare
                # code. Any other failure (429/503/…) is re-raised — the
                # bare code would hit the same rate limit, so retrying it
                # only burns time before the API fallback.
                if "no subtitle file produced" not in str(e):
                    raise
            return fetch_one(lang)
        return fetch_one(lang)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fetch_original_api(vid):
    """Fallback: youtube-transcript-api (works on a residential PC;
    blocked from datacenter IPs). Returns raw segments. Each call gets
    one retry on transient YouTube errors (429/500/502/503)."""
    if YouTubeTranscriptApi is None:
        raise RuntimeError("youtube-transcript-api not installed")
    api = YouTubeTranscriptApi()
    fetched = None
    last_err = None
    for lg in ["en", "hi", "ur"]:
        try:
            fetched = _api_try(lambda: api.fetch(vid, languages=[lg]))
            break
        except Exception as e:
            last_err = e
    if fetched is None:
        try:
            tlist = _api_try(lambda: api.list(vid))
            first = next(iter(tlist), None)
            if first is not None:
                fetched = _api_try(lambda: first.fetch())
        except Exception as e:
            last_err = e
    if fetched is None:
        raise RuntimeError("no transcript for this video: %s" % (last_err,))
    segs = fetched.to_raw_data()
    return ([{"start": s["start"], "duration": s["duration"], "text": s["text"]}
             for s in segs],
            fetched.language_code, bool(fetched.is_generated))


# --- transcript cache --------------------------------------------------
def _cache_path(vid, lang):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", vid)
    return TRANSCRIPTS_DIR / f"{safe}_{lang}.json"


def _load_transcript_cache(vid, lang):
    p = _cache_path(vid, lang)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_transcript_cache(vid, lang, data):
    try:
        _cache_path(vid, lang).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# --- Gemini translation (REST, no new dependency) ----------------------
# gemini-flash-lite-latest is the translation model: gemini-3.5-flash is
# severely degraded (40s+ per call, translations time out), while the lite
# model answers the same prompts in ~1s (verified 2026-11-18). The 3.5
# model stays as a fallback if the lite alias ever 404s for a key.
_GEMINI_MODEL = "gemini-flash-lite-latest"
_GEMINI_MODEL_FALLBACK = "gemini-3.5-flash"


def _gemini_key(flask_request):
    return ((flask_request.headers.get("X-Gemini-Key") or "").strip()
            or os.environ.get("GEMINI_API_KEY", "").strip())


def _gemini_generate(prompt, api_key, timeout=90):
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2},
    }).encode("utf-8")
    models = [_GEMINI_MODEL, _GEMINI_MODEL_FALLBACK]
    raw = None
    last_err = None
    for attempt in range(3):
        if attempt:
            time.sleep(5 if attempt == 1 else 15)
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{models[0]}:generateContent?key=" + urllib.parse.quote(api_key))
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            raw = _http_bytes(req, timeout=timeout)
            break
        except Exception as e:
            last_err = e
            if "404" in str(e) and len(models) > 1:
                models.pop(0)  # lite alias unknown for this key: fall back
                continue
            if not _is_retryable_http(e):
                raise
    else:
        raise RuntimeError(
            "Gemini is temporarily unavailable (%s) — please retry in a "
            "minute" % (str(last_err)[:80],))
    data = json.loads(raw.decode("utf-8", "replace"))
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts)


def _gemini_translate(texts, api_key, target_name):
    """Translate a list of transcript lines; returns a list of the same
    length, in the same order. Raises on failure or line-count mismatch
    (after one retry with numbered lines)."""
    out = []
    CHUNK = 100
    for i in range(0, len(texts), CHUNK):
        chunk = texts[i:i + CHUNK]
        prompt = (
            f"Translate these transcript lines into {target_name}. "
            "Keep the exact same number of lines, in the same order, one line "
            "per output line. Do not add, remove, merge or split lines. Do not "
            "add any explanation, numbering or extra text.\n\n" + "\n".join(chunk))
        res = _gemini_generate(prompt, api_key)
        lines = res.split("\n")
        while lines and not lines[-1].strip():
            lines.pop()
        while lines and not lines[0].strip():
            lines.pop(0)
        if len(lines) != len(chunk):
            numbered = "\n".join(f"{j + 1}. {t}" for j, t in enumerate(chunk))
            prompt2 = (
                f"Translate these numbered transcript lines into {target_name}. "
                "Reply with the same numbers, one translated line per number, "
                "nothing else.\n\n" + numbered)
            res2 = _gemini_generate(prompt2, api_key)
            lines2 = []
            for l in res2.splitlines():
                m = re.match(r"\s*\d+[.)]\s?(.*)", l)
                if m and m.group(1).strip():
                    lines2.append(m.group(1))
            if len(lines2) == len(chunk):
                lines = lines2
            else:
                raise RuntimeError(
                    "Gemini returned %d lines for %d input lines"
                    % (len(lines), len(chunk)))
        out.extend(lines)
    return out


# --- transcript assembly -----------------------------------------------
def _get_source_merged(vid):
    """Original-track transcript, merged into sentences (cached)."""
    cached = _load_transcript_cache(vid, "original")
    if cached is not None:
        return cached
    src_lang = _source_lang(vid)
    segs = None
    lang_code = src_lang or "auto"
    if src_lang:
        try:
            segs = _download_subs_vtt(vid, src_lang, native=True)
        except Exception:
            segs = None
    if not segs:
        segs, lang_code, _gen = _fetch_original_api(vid)
    result = {
        "video_id": vid,
        "language": LANG_LABELS["original"],
        "language_code": lang_code,
        "is_generated": True,
        "source_language": lang_code,
        "segments": _merge_sentences(segs),
    }
    _save_transcript_cache(vid, "original", result)
    return result


def _get_transcript(vid, lang, flask_request):
    if lang == "original":
        return _get_source_merged(vid)
    if lang == "roman":
        key = _gemini_key(flask_request)
        if not key:
            raise _KeyRequired()
        src = _get_source_merged(vid)
        texts = [s["text"] for s in src["segments"]]
        roman = _gemini_translate(
            texts, key,
            "natural Roman Urdu (Urdu written in Latin/Roman letters, the way "
            "people in Pakistan write it — e.g. 'Chandrashekhar Azad ka antim "
            "balidan'). Keep names as spoken.")
        segs = [{**s, "text": t} for s, t in zip(src["segments"], roman)]
        return {"video_id": vid, "language": "Roman Urdu",
                "language_code": "roman", "is_generated": False,
                "source_language": src.get("source_language"),
                "segments": segs}
    # en / hi / ur — Gemini first when a key is set (it translates the
    # already-fetched original; YouTube's auto-translate endpoint
    # rate-limits translated tracks with HTTP 429 while originals load
    # fine, and the transcript-API library is IP-blocked / PO-gated).
    # YouTube auto-translate stays as the fallback (and the only lane
    # when no Gemini key is set). If both lanes fail, the error names
    # both so the cause is visible in one line.
    src = _get_source_merged(vid)
    if src.get("source_language") == lang:
        return src
    key = _gemini_key(flask_request)
    gemini_err = None
    if key:
        try:
            texts = [s["text"] for s in src["segments"]]
            translated = _gemini_translate(texts, key, LANG_LABELS[lang])
            segs = [{**s, "text": t} for s, t in zip(src["segments"], translated)]
            return {"video_id": vid, "language": LANG_LABELS[lang],
                    "language_code": lang, "is_generated": True,
                    "source_language": src.get("source_language"),
                    "segments": segs}
        except Exception as e:
            gemini_err = e
    try:
        raw = _download_subs_vtt(vid, lang)
        if not raw:
            raise RuntimeError("empty subtitle track")
        segs = _merge_sentences(raw)
    except Exception as e:
        if key:
            raise RuntimeError(
                "translation failed — Gemini: %s; YouTube auto-translate: "
                "%s. Please retry in a few minutes."
                % (str(gemini_err)[:110], str(e)[:110]))
        raise RuntimeError("auto-translate failed (%s); add a Gemini API "
                           "key in Settings to translate instead" % (str(e)[:120],))
    return {"video_id": vid, "language": LANG_LABELS[lang],
            "language_code": lang, "is_generated": True,
            "source_language": src.get("source_language"),
            "segments": segs}


@app.route("/api/transcript")
def api_transcript():
    vid = (request.args.get("video_id") or "").strip()
    lang = (request.args.get("lang") or "original").strip().lower()
    if lang in ("roman-urdu", "romanurdu"):
        lang = "roman"
    if lang not in LANG_LABELS:
        lang = "original"
    if not vid:
        return jsonify({"error": "video_id required"}), 400
    cached = _load_transcript_cache(vid, lang)
    if cached is not None:
        return jsonify(cached)
    try:
        result = _get_transcript(vid, lang, request)
    except _KeyRequired:
        return jsonify({
            "error": "gemini_key_required",
            "detail": "Roman Urdu needs a Gemini API key. Add it in Settings "
                      "(gear icon, top right)."}), 400
    except Exception as e:
        return jsonify({"error": "transcript failed",
                        "detail": str(e)[:300]}), 502
    _save_transcript_cache(vid, lang, result)
    return jsonify(result)


# --------------------------------------------------------------- export
def _set_job(job_id, **kw):
    with jobs_lock:
        jobs.setdefault(job_id, {}).update(kw)


@app.route("/api/health")
def api_health():
    ff = _ffmpeg_dir()
    return jsonify({
        "ffmpeg": bool(ff) or bool(shutil.which("ffmpeg")),
        "ffmpeg_dir": ff,
        "download_dir": str(DOWNLOAD_DIR),
        "gemini_key_set": bool(os.environ.get("GEMINI_API_KEY")),
        "version": APP_VERSION,
    })


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    global DOWNLOAD_DIR
    cfg = _load_config()
    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        if "download_dir" in data:
            raw = (data.get("download_dir") or "").strip()
            if raw:
                try:
                    p = Path(raw).expanduser()
                    p.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    return jsonify({"error": "bad folder", "detail": str(e)[:200]}), 400
                cfg["download_dir"] = str(p)
            else:
                cfg.pop("download_dir", None)
        if data.get("default_quality") in QUALITIES:
            cfg["default_quality"] = data["default_quality"]
        _save_config(cfg)
        DOWNLOAD_DIR = _resolve_download_dir()
    return jsonify({"download_dir": str(DOWNLOAD_DIR),
                    "default_quality": cfg.get("default_quality", "720")})


def _open_path(path, select=False):
    """Open a file/folder with the OS (Explorer on Windows)."""
    import subprocess
    try:
        if sys.platform.startswith("win"):
            if select and Path(path).is_file():
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)] if select else ["open", str(path)])
        else:
            target = Path(path).parent if Path(path).is_file() else path
            subprocess.Popen(["xdg-open", str(target)])
        return True
    except Exception:
        return False


@app.route("/api/open", methods=["POST"])
def api_open():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    if name:
        target = (DOWNLOAD_DIR / Path(name).name)
        if not target.exists():
            return jsonify({"error": "file not found"}), 404
        ok = _open_path(target, select=bool(data.get("reveal", True)))
    else:
        ok = _open_path(DOWNLOAD_DIR)
    return jsonify({"ok": ok})


@app.route("/api/recent")
def api_recent():
    files = []
    try:
        for pat in ("*.mp4", "*.mp3", "*.m4a"):
            for p in DOWNLOAD_DIR.glob(pat):
                try:
                    st = p.stat()
                except OSError:
                    continue
                files.append({"name": p.name, "size": st.st_size,
                              "mtime": st.st_mtime})
    except Exception:
        pass
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return jsonify({"download_dir": str(DOWNLOAD_DIR), "files": files[:8]})


def _run_export(job_id, video_id, title, start, end, mode, precise, quality):
    url = f"https://www.youtube.com/watch?v={video_id}"
    safe_title = safe_filename(title or video_id)
    audio = quality == "audio"
    tag = {"audio": "audio", "best": "best"}.get(quality, quality + "p")
    suffix = (f"_{int(start)}-{int(end)}s" if mode == "clip" else "_full") + f"_{tag}"
    outtmpl = str(DOWNLOAD_DIR / f"{safe_title}{suffix}.%(ext)s")

    def is_cancelled():
        with jobs_lock:
            return bool(jobs.get(job_id, {}).get("cancel"))

    def progress_hook(d):
        if is_cancelled():
            raise _Cancelled()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            upd = {}
            if total:
                upd["progress"] = round(min(done / total * 100, 99.0), 1)
            if d.get("speed"):
                upd["speed"] = int(d["speed"])
            if d.get("eta") is not None:
                upd["eta"] = int(d["eta"])
            if upd:
                _set_job(job_id, **upd)

    def pp_hook(d):
        if is_cancelled():
            raise _Cancelled()
        if d.get("status") == "started":
            _set_job(job_id, message="Processing…", progress=99.0)

    def build_opts(nocheck=False, fmt=None):
        opts = {**_ydl_base_opts(), "quiet": False, "noprogress": True,
                "format": fmt or _format_for(quality), "outtmpl": outtmpl,
                "nocheckcertificate": nocheck,
                "progress_hooks": [progress_hook],
                "postprocessor_hooks": [pp_hook]}
        if audio:
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                       "preferredcodec": "mp3",
                                       "preferredquality": "192"}]
        else:
            opts["merge_output_format"] = "mp4"
        if mode == "clip":
            opts["download_ranges"] = (
                lambda info, ydl: [{"start_time": float(start),
                                     "end_time": float(end)}])
            if precise and not audio:
                opts["force_keyframes_at_cuts"] = True
        return opts

    _set_job(job_id, status="downloading", progress=0, message="Downloading…")
    # Clip / audio export needs ffmpeg. Fail fast with a clear message.
    if (mode == "clip" or audio) and not _ffmpeg_dir() and not shutil.which("ffmpeg"):
        _set_job(job_id, status="error",
                 error="ffmpeg not found. Put ffmpeg.exe (and ffprobe.exe) next to "
                       "YT-Clip-Tool.exe and try again — they are in the portable ZIP.")
        return
    try:
        try:
            with yt_dlp.YoutubeDL(build_opts(False)) as ydl:
                ydl.download([url])
        except _Cancelled:
            raise
        except Exception as e:
            if _is_ssl_error(e):
                _set_job(job_id, message="Retrying download…")
                with yt_dlp.YoutubeDL(build_opts(True)) as ydl:
                    ydl.download([url])
            elif "requested format" in str(e).lower() and not audio:
                with yt_dlp.YoutubeDL(build_opts(False, FORMAT_FALLBACK)) as ydl:
                    ydl.download([url])
            else:
                raise
    except _Cancelled:
        for junk in DOWNLOAD_DIR.glob(f"{safe_title}{suffix}.*"):
            if junk.suffix in (".part", ".ytdl", ".temp") or ".part" in junk.name:
                try:
                    junk.unlink()
                except OSError:
                    pass
        _set_job(job_id, status="cancelled", message="Cancelled.")
        return
    except Exception as e:
        if is_cancelled():
            _set_job(job_id, status="cancelled", message="Cancelled.")
            return
        _set_job(job_id, status="error",
                 error="Download failed. " + str(e)[-300:])
        return

    produced = sorted(DOWNLOAD_DIR.glob(f"{safe_title}{suffix}.*"),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    produced = [p for p in produced if p.suffix.lower() in (".mp4", ".mp3", ".m4a", ".mkv", ".webm")]
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
    quality = str(data.get("quality") or _load_config().get("default_quality") or "720")
    if quality not in QUALITIES:
        quality = "720"
    job_id = uuid.uuid4().hex[:12]
    _set_job(job_id, status="queued", progress=0, message="Starting…",
             video_id=video_id, mode=mode, quality=quality)
    t = threading.Thread(target=_run_export,
                         args=(job_id, video_id, title, start, end, mode, precise, quality),
                         daemon=True)
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/api/export/cancel/<job_id>", methods=["POST"])
def api_export_cancel(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            return jsonify({"error": "unknown job"}), 404
        job["cancel"] = True
    return jsonify({"ok": True})


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


def _free_port(preferred=5000, tries=20):
    import socket
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
            try:
                sk.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


if __name__ == "__main__":
    ff = _ffmpeg_dir()
    port = _free_port(5000)
    print(f"YT Clip Tool v{APP_VERSION}")
    print(f"  ffmpeg    : {ff or ('on PATH' if shutil.which('ffmpeg') else 'NOT FOUND')}")
    print(f"  downloads : {DOWNLOAD_DIR}")
    print(f"  open      : http://127.0.0.1:{port}   (close this window to quit)")
    if getattr(sys, "frozen", False):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
