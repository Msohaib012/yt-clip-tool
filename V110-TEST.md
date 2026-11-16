# YT Clip Tool v1.1.0 — Test Report

**Date:** 2026-11-16 · **Test video:** Kakori, `hikXohQRHhI` (Mighty Monk) ·
**Test env:** this VM, Flask on 127.0.0.1:5000, test venv (flask + yt-dlp + youtube-transcript-api)

## What was tested — all passed unless noted

| # | Check | Result |
|---|---|---|
| 1 | `python -m py_compile app.py` | ✅ PASS |
| 2 | Inline JS `node --check` | ✅ PASS |
| 3 | `/` loads (HTTP 200, 39 KB) | ✅ PASS |
| 4 | `/api/health` — ffmpeg ready, version 1.1.0 | ✅ PASS |
| 5 | `/api/suggest?q=kakori` — 10 real YouTube suggestions | ✅ PASS (curl + headless browser dropdown) |
| 6 | `/api/search?q=kakori` — 10 results, first = Kakori video | ✅ PASS |
| 7 | Transcript Original — 148 merged lines, source hi | ✅ PASS |
| 8 | Sentence merging — avg 13.1s, max 19.8s, 0 lines > 30s, only the final 1.1s tail under 10s | ✅ PASS (first merge attempt gave 368 lines / avg 5.4s — fixed: now flush at sentence boundary only once the line is ≥ 10s) |
| 9 | Transcript English — 134 merged lines, real YouTube auto-translate | ✅ PASS |
| 10 | Transcript Hindi — 148 lines, served from original (source is Hindi), instant | ✅ PASS |
| 11 | Transcript Urdu — YouTube auto-translate | ⚠️ **429 on this VM** (datacenter IP rate-limited; retry-once also 429). Error message correctly tells the user to add a Gemini key for the translate fallback. Expected to work on a residential PC. |
| 12 | Roman Urdu without key — HTTP 400 `gemini_key_required` + in-UI "Add Gemini key" prompt | ✅ PASS |
| 13 | Roman Urdu prompt via Gemini connector — 10 lines in → 10 lines out, natural Roman Urdu ("9 August 1925 raat ke ghane andhere ko cheerte hue…") | ✅ PASS (prompt + line-count logic verified; app's REST path uses the same prompt and model `gemini-3.5-flash`) |
| 14 | Roman Urdu with a fake key — HTTP 502, clean "HTTP Error 400: Bad Request", no crash | ✅ PASS |
| 15 | Export 10s clip (fast mode) — valid 720p H.264+AAC MP4, 10.07s, 827 KB, ~30s | ✅ PASS |
| 16 | `/api/recent` lists the export; `/api/download/<job>` HTTP 200 | ✅ PASS |
| 17 | Headless Chromium UI test — suggestions dropdown (10 items), search cards, workspace, 148 transcript lines, EN switch (134 lines), Roman key prompt, Follow button on manual scroll, Follow click re-enables, Settings modal, "Precise cut (frame-accurate)" label, Copy button, Recent Exports visible | ✅ PASS (all steps; screenshots: /tmp/ytclip_home.png, /tmp/ytclip_work.png, /tmp/ytclip_settings.png) |
| 18 | Transcript cache — `transcripts/{video}_{lang}.json` written; re-switch is instant (EN re-fetch 0.006s) | ✅ PASS |

## Bugs found and fixed during testing

1. **Merge too fragmented (368 lines, avg 5.4s, 75 lines < 3s).** First version flushed at the first sentence end, splitting sentences at the 10s boundary. Fixed: accumulate sentences until the line is ≥ 10s, then cut at the next sentence boundary; cut mid-sentence only if a sentence runs 10s+ without completing; cap 30s (comma split). Result: 148 lines, avg 13.1s.
2. **Follow button never appeared on scrollbar drag.** Listeners only covered wheel/touch/keydown. Fixed: added a `scroll` listener with a `programmaticScroll` flag so the app's own auto-scroll doesn't trip it, but any user scroll (incl. dragging the scrollbar) turns follow off.

## Honest limitations (VM-only, not app bugs)

- **Urdu auto-translate 429:** this VM's datacenter IP is rate-limited by YouTube timedtext right now (EN worked minutes earlier on the same video, then UR 429'd). On his residential PC it should work; if it fails there, the Gemini fallback (with key) covers it.
- **Roman Urdu end-to-end through the app was NOT tested with a real key** (no key is visible on this VM by design). The prompt and line-count logic were verified through the Gemini connector with the same model; the app's REST call with a fake key fails cleanly.
- **YouTube player iframe shows black in headless Chromium here** (VM proxy blocks youtube.com embeds — ERR_TUNNEL_CONNECTION_FAILED). The player code is unchanged from v1.0.1; it will load on his PC.
- **Console noise in the headless test:** favicon 404 (fixed with an inline `data:,` favicon), the expected 400 from the no-key Roman request, and the YouTube embed tunnel errors above. None are app errors.

## Files changed (no push, no .exe build — parent does that)

- `app.py` — suggestions route, transcript languages (yt-dlp auto-subs + Gemini REST), sentence merging, transcript cache, OS Downloads folder, `/api/recent`, health v1.1.0. No new dependency (Gemini via plain `urllib` REST) — `requirements.txt` and `.github/workflows/build-exe.yml` unchanged.
- `static/index.html` — suggestions dropdown, language switcher, followMode + Follow button, Copy/.txt transcript, Recent Exports, Settings modal (Gemini key in localStorage), "Precise cut (frame-accurate)" + plain explanation, sticky transcript panel, UI polish (v1.1.0 tag, gear button).
- `.gitignore` — added `transcripts/`.
- `README.md` — v1.1.0 features + Downloads folder + Roman Urdu key note.
- `V110-TEST.md` — this report.
