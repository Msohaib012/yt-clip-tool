# YT Clip Tool — Research: Search Suggestions + Transcript Language Switching

**Date:** 2026-11-16 · **Scope:** research only, no files edited in the app itself.
**Verified live from this VM where marked ✅; web-sourced where marked 🔎.**

---

## 1. YouTube search suggestions (autocomplete)

### Verdict
Use the **free, keyless Google Suggest endpoint with `client=youtube&ds=yt`**, called
**through the Flask backend** (not directly from the browser — see CORS note).
The official YouTube Data API v3 has **no** suggestions/autocomplete endpoint (confirmed,
§1.3). This is the same endpoint YouTube's own search box uses.

### 1.1 The endpoint (✅ verified live 2026-11-16)

```
GET https://suggestqueries.google.com/complete/search?client=youtube&ds=yt&hl=en&gl=us&q=<url-encoded query>
```

Alternate host, same response (what YouTube's box actually calls):

```
GET https://suggestqueries-clients6.youtube.com/complete/search?client=youtube&gs_ri=youtube&ds=yt&hl=en&gl=US&q=<query>
```

Parameters:

| Param | Meaning |
|---|---|
| `client=youtube` | YouTube-style suggestions (JSONP wrapper). Use `client=firefox` for **clean JSON** instead (see below). |
| `ds=yt` | Data source = YouTube search (without it you get Google web suggestions). |
| `hl=en` | Suggestion language (`hl=ur` / `hl=hi` for Urdu/Hindi suggestions). |
| `gl=us` | Country (`gl=pk` for Pakistan-flavoured suggestions). |
| `q=` | The partial query, URL-encoded. |

No API key, no login, no quota. HTTP 200 from this VM's datacenter IP (tested with
`q=kakori` — returned 15 suggestions incl. "kakori train robbery", "kakori kebab recipe").

### 1.2 Response format — two flavours

**`client=youtube` → JSONP** (what the search box uses):

```js
window.google.ac.h(["kakori",[["kakori kebab recipe",0,[512]],["kakori train robbery",0,[512]], ... ],{"k":1}])
// suggestions = parsed[1].map(item => item[0])
```

**`client=firefox` → clean JSON** (recommended — trivial to parse, same suggestions):

```json
["kakori",["kakori kebab","kakori village theme","kakori","kokiri forest","kakori recipe","kakori village","kakori web series","kakori train robbery","kakariko village","kakariko village theme"],[],{"google:suggestsubtypes":[[512],[512], ...]}]
// suggestions = parsed[1]
```

### 1.3 CORS — why it must go through Flask (✅ verified)

The endpoint sends **no `Access-Control-Allow-Origin` header**. A direct `fetch()` from
the app's page will be **CORS-blocked**. Two options:

- **Recommended: Flask proxy.** Frontend calls `/api/suggest?q=...`, Flask fetches the
  endpoint server-side (urllib/requests) and returns JSON. Also lets you debounce,
  cache, and strip the JSONP wrapper in one place.
- Fallback (not recommended): JSONP via a `<script>` tag — works (script tags bypass
  CORS) but is hacky and hard to cancel/timeout.

### 1.4 Official YouTube Data API — no suggestions endpoint (🔎 confirmed)

YouTube Data API v3 has **no autocomplete/suggestions resource**. `search.list`
(`GET https://www.googleapis.com/youtube/v3/search?part=snippet&q=...&key=...`) returns
**actual search results, not predictive suggestions**, and costs **100 quota units per
call** (default 10,000/day = ~100 searches/day). Wrong tool for autocomplete — do not
use it for this. The `captions` API is likewise irrelevant here (it only downloads
captions for videos you own, OAuth, 200 units/call).

### 1.5 GitHub repos that use this endpoint (🔎)

- **n-ce/ytify** — `search-suggestions.ts` edge function: general YouTube suggestions via
  `suggestqueries-clients6.youtube.com` (`client=youtube`, `ds=yt`), no key.
- **DeepEyeCrypto/DeepEyeMusic** (`SEARCH_ENGINE.md`) — remote suggestion pipeline on
  `suggestqueries.google.com/complete/search?client=youtube&ds=yt&q=...`, 300 ms debounce.
- Keyword-research skills (arturseo-geo/claude-code-skills, vicgupta/hole-in-one) — same
  endpoint, same `client=youtube&ds=yt` form.

### 1.6 Minimal implementation (Flask proxy + JS)

```python
# app.py — new route
import json, urllib.request, urllib.parse

@app.route('/api/suggest')
def suggest():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    url = ("https://suggestqueries.google.com/complete/search"
           "?client=firefox&ds=yt&hl=en&gl=pk&q=" + urllib.parse.quote(q))
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=5) as r:
        data = json.loads(r.read().decode('utf-8'))
    return jsonify(data[1][:10])   # data[1] = suggestion strings
```

```js
// static/index.html — debounced autocomplete under the search box
let suggestTimer;
searchInput.addEventListener('input', () => {
  clearTimeout(suggestTimer);
  const q = searchInput.value.trim();
  if (q.length < 2) { hideSuggestions(); return; }
  suggestTimer = setTimeout(async () => {
    const res = await fetch('/api/suggest?q=' + encodeURIComponent(q));
    const items = await res.json();
    renderSuggestionDropdown(items);   // click a suggestion -> run search
  }, 250);  // 250 ms debounce — do not fire on every keystroke
});
// Hide on Escape / click-outside; arrow keys move selection; Enter picks it.
```

Notes: debounce 250–300 ms; minimum 2 chars; cap 10 items; cache repeat queries in a
small dict (same query twice = no second network call). The endpoint is undocumented
and could change, but it has been stable for years — wrap the fetch in try/except and
fail silently (search box keeps working without suggestions).

---

## 2. Transcript language switching (Original / English / Hindi / Urdu / Roman Urdu)

### Verdict (short version)

| Language | Source | Cost | Quality |
|---|---|---|---|
| **Original** | yt-dlp auto-subs, source track | Free | As YouTube heard it |
| **English** | YouTube **auto-translate** via yt-dlp (`--sub-langs en`) | Free | Machine translation, decent |
| **Hindi** | YouTube **auto-translate** via yt-dlp (`--sub-langs hi`) | Free | Machine translation, decent |
| **Urdu** | YouTube **auto-translate** via yt-dlp (`--sub-langs ur`) | Free | Machine translation, decent |
| **Roman Urdu** | **Gemini** (translate/transliterate the transcript text, keep timestamps) | Gemini API key (user already has one) | Best — natural Roman Urdu spelling |

No new API key needed for English/Hindi/Urdu — they come free from YouTube.
Roman Urdu is the only language that needs AI, because **YouTube has no Roman Urdu
caption track at all**.

### 2.1 What YouTube actually offers (✅ verified on the Kakori video)

`yt-dlp --list-subs "https://www.youtube.com/watch?v=hikXohQRHhI"` prints two tables:

- **"Available subtitles"** (creator-uploaded): this video has **none**.
- **"Available automatic captions"**: one original track (`hi-orig` = Hindi Original)
  **plus ~130 auto-translate target languages**, including:

```
en   English
hi   Hindi
ur   Urdu
hi-orig  Hindi (Original)
```

So for any video that has an auto-generated track, YouTube will machine-translate it
into Hindi, Urdu, English (and ~127 more) **on the fly, for free**. This is the same
auto-translate the YouTube player offers under Settings → Subtitles → Auto-translate.

### 2.2 Fetching a translated transcript with yt-dlp (✅ method proven, ⚠️ VM rate-limit)

```bash
# list what exists first (no download)
yt-dlp --list-subs "https://www.youtube.com/watch?v=VIDEO_ID"

# fetch ONLY the Urdu auto-translated track, no video
yt-dlp --skip-download --write-auto-subs --sub-langs ur --sub-format vtt \
       -o "/tmp/transcript.%(id)s" "https://www.youtube.com/watch?v=VIDEO_ID"
# -> /tmp/transcript.VIDEO_ID.ur.vtt

# same for Hindi / English
yt-dlp --skip-download --write-auto-subs --sub-langs hi --sub-format vtt -o "..." "URL"
yt-dlp --skip-download --write-auto-subs --sub-langs en --sub-format vtt -o "..." "URL"

# original track only (whatever language the video is)
yt-dlp --skip-download --write-auto-subs --sub-format vtt -o "..." "URL"
```

**Important caveat (honest):** the fetch itself hit `HTTP Error 429: Too Many Requests`
twice on this VM today (datacenter IP is rate-limited for the timedtext download).
But the method is **proven, not broken**: the FuseClip spike earlier today fetched the
same video's Hindi auto-subs via yt-dlp in 8.1 s. The 429 is a this-VM/IP problem —
on the user's PC (residential IP) it should work. The app should retry once after a
few seconds on 429, then fall back to the original track + Gemini translation.

`youtube-transcript-api` is the alternative fetcher and has the same feature built in
(`transcript.translate('ur')`, `transcript.translation_languages`), but it is **blocked
on this VM** (RequestBlocked — datacenter IP) while yt-dlp auto-subs still works, so
**yt-dlp stays the primary transcript fetcher**. Keep youtube-transcript-api only as
a fallback on the PC.

### 2.3 Urdu and Hindi — YouTube auto-translate vs Gemini

- **Use YouTube auto-translate first (free, instant, no key).** Quality is Google
  machine translation: good enough for reading a transcript, occasional awkward
  phrasing. For a clip tool this is the right default.
- **Gemini is the upgrade, not the default**, for these two: only if the user
  complains about translation quality. Gemini translation of the same text is
  noticeably more natural, but costs an API call per switch. Do not spend Gemini
  calls on Hindi/Urdu by default when YouTube gives them free.
- **Rule:** if the requested language **equals the source language**, serve the
  original track (no translation at all). Detect the source language from
  `--list-subs` / the info JSON (`language` field, or the `*-orig` track).

### 2.4 Roman Urdu — the hard one

YouTube has **no Roman Urdu track** and no auto-translate target for it. Three
candidate methods were checked:

#### (a) Gemini LLM translation/transliteration — ✅ RECOMMENDED

Prompt Gemini with the transcript text (chunked, ~100 lines per call) and ask for
**natural Roman Urdu** (the way people actually write: "Chandrashekhar Azad ka
antim balidan"), preserving the line breaks so timestamps stay aligned — translate
**text only, keep the original segment start times**. Works uniformly from Hindi,
Urdu, or English source text, and it handles mixed Hinglish/Urdu speech (very
common in the user's videos) far better than any rule-based tool.

- Needs a Gemini API key — **the user already has Gemini keys** (used by the
  FlickAtlas pipeline / Gemini connector). The tool just needs a key field in
  settings (or an env var); do **not** hard-code a key.
- Cost: one Gemini call per transcript (chunk long transcripts). Cache the result
  per video+language so switching back is free.

#### (b) Rule-based transliteration libraries — ⚠️ partial, not recommended as primary

| Library | What it does | Verdict for Roman Urdu |
|---|---|---|
| `indic-transliteration` (PyPI, MIT, `pip install indic-transliteration`) | Devanagari → ITRANS / HK / IAST (scholarly romanization) | **Hindi only** (no Urdu/Arabic script support). Output is ITRANS — e.g. long vowels marked, "chandra shekhara" style — **not** natural Roman Urdu spelling. Usable only with a custom post-processing layer (see srota below). Lightweight, offline, free. |
| `ai4bharat-transliteration` / IndicXlit (AI4Bharat, IIT Madras) | Neural transliteration, 21 languages **incl. Hindi + Urdu**, Native→Roman, trained on colloquial Roman text — natural spelling | **Best offline option in theory**, but the PyPI package (`0.5.0.1`) is old (~2020) and the neural models need **PyTorch (~1.4 GB)** — far too heavy for a PyInstaller `.exe` and slow per word (~115 ms/word). Not practical for this tool. |
| `urduhack` (PyPI) | Urdu NLP library; `normalize` is **normalization, not transliteration** | **Do not use.** Requires TensorFlow (huge), and its GitHub issues report romanization as broken/missing ("this does not support romanization", keras import errors). Dead end. |
| `uroman` (PyPI) | Universal romanizer for any script | Academic/standardized romanization, not natural Roman Urdu spelling. Not the right output. |

**Bottom line on transliteration:** no lightweight offline library produces *natural*
Roman Urdu from Urdu script. For Hindi source, `indic-transliteration` + custom
cleanup rules can work offline, but Gemini is better and simpler. **Gemini wins.**

#### (c) Closest proven repo pattern (🔎)

- **Jarus77/srota** — Hinglish ASR post-processing pipeline: `indic_transliteration`
  Devanagari → ITRANS, then a custom `_itrans_to_hinglish()` function that rewrites
  ITRANS into natural Hinglish spelling (plus loanword recovery). This is the proven
  pattern *if* an offline Hindi→Roman path is ever wanted — copy the two-stage
  approach (library + custom word-map), don't expect the library alone to sound natural.
- No turnkey GitHub repo exists that translates YouTube transcripts to Roman Urdu —
  the combination is **yt-dlp (fetch) + Gemini (Roman Urdu)**. That is the
  recommendation, not a gap to keep searching for.

### 2.5 Recommended architecture (Flask, Windows PC)

```
User picks video
  └─> /api/transcript?video_id=...&lang=original
        ├─ yt-dlp --skip-download --write-auto-subs --sub-format vtt
        │   (detect source lang from --list-subs / info JSON; keep the VTT)
        └─ parse VTT -> [{start, end, text}]  (existing parser, reuse it)

User switches language (dropdown: Original / English / Hindi / Urdu / Roman Urdu)
  ├─ target == source lang        -> serve the original segments (no work)
  ├─ target in {en, hi, ur}       -> /api/transcript?lang=<code>
  │     ├─ cache hit (video+lang) -> serve cached segments
  │     └─ else yt-dlp --write-auto-subs --sub-langs <code> --sub-format vtt
  │        (retry once on 429; on failure, fall back to Gemini translate)
  └─ target == roman-urdu         -> /api/transcript?lang=roman
        ├─ cache hit -> serve cached segments
        └─ else Gemini: translate the ORIGINAL segment texts to natural Roman Urdu,
           one chunk per call, keep start/end times untouched, cache result
```

Practical details:

- **Cache** every fetched/translated transcript per `video_id + lang` (JSON file next
  to the app or in a `transcripts/` folder) — switching languages must feel instant
  the second time, and re-switching must not re-spend Gemini calls.
- **Timestamps belong to the source.** Translation (YouTube or Gemini) is applied to
  text only; segment start/end times are copied unchanged. This keeps clip cutting
  and the synced highlight working in every language.
- **Sentence merging (separate feedback item, not this research):** the user also
  asked for one complete sentence per transcript line (min 10 s if a sentence can't
  complete). Do the merging **once on the source track**, then translate the merged
  lines — never translate fragments and merge afterwards.
- **Gemini key:** settings field / env var `GEMINI_API_KEY`. The user already has
  keys; the build must not embed any key in the repo or the `.exe`.
- **Fallback chain on any failure:** requested language → original track (always
  available) → error message only if even the original is missing.

### 2.6 Exact commands (copy-paste)

```bash
# discover tracks + source language
yt-dlp --list-subs "https://www.youtube.com/watch?v=VIDEO_ID"

# original auto-subs (VTT)
yt-dlp --skip-download --write-auto-subs --sub-format vtt \
  -o "transcript.%(id)s" "https://www.youtube.com/watch?v=VIDEO_ID"

# translated track (Hindi / Urdu / English)
yt-dlp --skip-download --write-auto-subs --sub-langs hi --sub-format vtt \
  -o "transcript.%(id)s" "https://www.youtube.com/watch?v=VIDEO_ID"
#   --sub-langs ur   for Urdu
#   --sub-langs en   for English
```

```python
# Gemini Roman Urdu (text only, timestamps preserved) — sketch
import google.generativeai as genai  # or the existing gemini helper in the project
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
model = genai.GenerativeModel("gemini-2.0-flash")  # or the model already in use
prompt = (
  "Transliterate/translate these Hindi/Urdu transcript lines into natural Roman Urdu "
  "(the way Pakistanis write Urdu in Latin letters, e.g. 'Chandrashekhar Azad ka antim "
  "balidan'). Keep the exact same number of lines, in the same order, one per line. "
  "Do not add, remove or merge lines.\n\n" + "\n".join(source_texts)
)
roman_texts = model.generate_content(prompt).text.splitlines()
segments = [{**seg, "text": t} for seg, t in zip(source_segments, roman_texts)]
```

---

## Sources

- Suggest endpoint: verified live via curl 2026-11-16 (both hosts, `client=youtube`
  JSONP and `client=firefox` clean JSON); CORS headers checked (none present).
- YouTube Data API has no suggestions endpoint: codestudy.net, quanticdata.io,
  keyapi.ai (all confirm `search.list` only, 100 units/call).
- yt-dlp subtitle flags: `--list-subs` output on `hikXohQRHhI` (auto-translate table
  incl. `hi`, `ur`, `en`, `hi-orig`); yt-dlp docs / techearl cheat sheet.
- youtube-transcript-api README (jdepoix/youtube-transcript-api): `.translate()`,
  `translation_languages`; datacenter-IP blocking note.
- indic-transliteration: PyPI + GitHub indic-transliteration/indic_transliteration_py
  (v2.3.61, MIT).
- AI4Bharat IndicXlit: PyPI `ai4bharat-transliteration`, GitHub AI4Bharat/IndicXlit
  (21 languages incl. `ur`; PyTorch weight noted via codemix-restore-shunyalabs).
- urduhack: PyPI + GitHub urduhack/urduhack issues (romanization broken, TF dep).
- srota Hinglish pipeline: GitHub Jarus77/srota (`HINGLISH_PIPELINE_SPEC.md`).
- ytify suggestions edge function: n-ce/ytify (DeepWiki).
