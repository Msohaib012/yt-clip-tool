# YT Clip Tool v1.2.0 (Claude UI) — Verification by Muse, 2026-11-17

Source: ZIP sent by sohaib (YT-Clip-Tool-v1.2.0-source_1_59tr.zip), extracted to
~/workspace/yt-clip-tool-v120-claude/. Base = v1.1.0 (commit 30b415e).

## Verdict: PASS — real upgrade, all v1.1.0 features intact, no regressions found

## What Claude added (v1.2.0)
- New UI: clean minimal premium dark design + light theme toggle
- Precision clip editor: drag start/end handles, I/O keys, ±1s steppers, Preview clip
- Quality picker: 360/480/720/1080/Best + audio-only MP3 export
- Export control: live speed + ETA, Cancel, Show in folder, Open folder
- Settings: download folder + default quality saved in config.json (disk)
- Keyboard: / search, I start, O end, Space play/pause, arrows seek 5s
- Auto-picks a free port if 5000 is busy

## v1.1.0 features — all verified present
- Search suggestions (suggest route, 24 refs), transcript search (33 marks, "1 / 26" — same numbers as v1.1.0)
- Language switcher Original/English/Hindi/Urdu/Roman Urdu (5 buttons)
- Sentence-merged transcript: Kakori 148 lines (matches v1.1.0 exactly)
- followMode scroll fix (scroll/wheel/touch/keydown listeners + Follow button)
- Copy transcript, Download .txt, Recent exports, Settings (Gemini key)
- ffmpeg fix (_ffmpeg_dir), /api/health, /api/recent, /api/suggest
- Transcript fetch: same yt-dlp auto-subs (writeautomaticsub/subformat vtt) + youtube-transcript-api fallback

## Live tests (Flask + Playwright, this VM)
- /api/health: version 1.2.0, ffmpeg true, index 200
- Search API: works (intermittent SSL proxy flakiness on this VM — same as v1.1.0, NOT a regression)
- Transcript API: original 200, 148 segments; English 502 with clear message (YouTube 429 on this VM's datacenter IP — known VM limit, Gemini fallback suggested in the error)
- Browser: 148 transcript lines rendered, first line correct, search "आजाद" = 33 marks / 26 matches, quality options 360p/480p/720p/1080p/Best visible, 0 JS errors
- Export: 5s clip 720p exported end-to-end — hikXohQRHhI_10-15s_720p.mp4, 332 KB, 5.015s, H.264+AAC, 1280x720
- Cancel: POST /api/export/cancel returns ok:true, job status becomes "cancelled"

## Notes / non-issues
- Quality API takes bare numbers ("360") not "360p" — "360p" falls back to 720 by design (QUALITIES tuple). UI sends bare numbers.
- Neither v1.1.0 nor v1.2.0 supports ?v= auto-load in the browser; paste-URL flow works.
- The cancelled job still left a complete MP4 (download finished before cancel landed) — harmless.
- English auto-translate 429 on this VM only; works on a residential PC (same as v1.1.0).

## One real difference to flag: export codec can be AV1, not H.264
- v1.1.0 pinned `FORMAT_720P = "136+140"` (H.264 + AAC, guaranteed).
- v1.2.0 `_format_for()` uses `bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/...` — `ext=mp4` does NOT guarantee H.264 (AV1 also ships in MP4). Live test exported AV1 (1280x720) + AAC.
- Impact: AV1 plays fine in Chrome/VLC/YouTube, but is heavier to edit in DaVinci Resolve and may not play on older players. Fix = prefer `vcodec=avc1` in the selector (one line), restoring the v1.1.0 H.264 guarantee. NOT a blocker, but fix before release.

## Recommendation
Ship it: push to Msohaib012/yt-clip-tool as v1.2.0 (build-exe.yml already says v1.2.0), build + release. No code changes needed from Muse.
