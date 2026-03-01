# 🦞 YTSumBot — Telegram YouTube Summarizer & Q&A Bot

> Built on **OpenClaw** · Powered by **Gemini 2.0 Flash** · CPU-only · English + Hindi, Tamil, Kannada, Telugu, Marathi

---

## What This Bot Does

Send any YouTube link → get an instant structured summary:

```
🎥 Video Title

📌 Key Points:
1. ...
2. ...
3. ...
4. ...
5. ...

⏱ Important Timestamps:
- 2:14 — ...
- 7:43 — ...

🧠 Core Takeaway:
...

💡 Who Should Watch This:
...
```

Then ask follow-up questions. The bot answers **strictly from the transcript** (no hallucinations). Switch to Hindi, Tamil, Kannada, Telugu, or Marathi anytime.

---

## Prerequisites

| Requirement | Version | How to get |
|---|---|---|
| **Node.js** | >= 22 | https://nodejs.org → LTS |
| **Python** | >= 3.11 | https://python.org |
| **Telegram Bot Token** | — | [@BotFather](https://t.me/BotFather) |
| **Gemini API Key** | — | [aistudio.google.com](https://aistudio.google.com/app/apikey) (free) |

> ⚠️ OpenClaw **requires Node 22+**, not 18 or 20. Check with `node --version`.

---

🧠 Advanced System Upgrades (v2)
1️⃣ Topic-Aware Segmentation (Semantic Chunking)
Instead of naive 800-character chunks, process_video.py now:
Splits transcript into 15-second windows
Embeds each window using all-MiniLM-L6-v2
Computes cosine similarity between adjacent windows
Detects sharp similarity drops (topic shifts)
Creates semantic topic boundaries
Why This Matters
Prevents mid-topic cuts
Improves retrieval precision
Better handling of podcasts & lectures
More coherent Q&A responses
This mimics real document segmentation used in production RAG systems.


2️⃣ Parent-Child Retrieval (Hierarchical RAG)
Each topic becomes:
Parent (~1500 chars) → rich context
Children (~400 chars) → precise search units stored in ChromaDB
Retrieval Flow
User question
      ↓
Embed query (MiniLM)
      ↓
ChromaDB finds best CHILD
      ↓
Return corresponding PARENT
      ↓
Gemini answers with full topic context
Benefits
🔍 Child = precise semantic match
🧠 Parent = complete topic context
❌ Avoids partial answers
✅ Improves long-video Q&A reliability
This design significantly improves retrieval quality over flat chunking.


3️⃣ CRAG-lite (Confidence-Aware RAG)


answer_question.py implements a lightweight Confidence-Retrieval-Augmented Generation loop.
Confidence Levels
Based on ChromaDB similarity distances:
Score	Meaning
🟢 High	Strong semantic alignment
🟡 Medium	Partial alignment
🔴 Low	Weak or ambiguous match
Self-Healing Retrieval
If confidence is low:
Gemini rewrites the user query
Retrieval runs again
Better context is used if found
Response displays:
_ (query auto-corrected) _
This prevents weak-context answers and increases robustness.


🤖 Enhanced User Experience
Smart Follow-Up Buttons
After every summary, users get tappable suggestions:
🔎 Ask about a topic
📌 Extract action points
🧠 Explain deeper
📊 Show stats
Improves engagement and conversational flow.


Automatic Language Detection
Language is auto-detected using Unicode script ranges.
Example:
हिंदी में बताओ → Hindi
தமிழில் சொல்லுங்கள் → Tamil
ಕನ್ನಡದಲ್ಲಿ ಹೇಳಿ → Kannada
తెలుగులో చెప్పండి → Telugu
मराठीत सांगा → Marathi
Manual switching still supported via /language.






Open Telegram → find your bot → send any YouTube URL.

**Test video** (short, has captions): `https://www.youtube.com/watch?v=dQw4w9WgXcQ`

---

## Bot Commands

| Message / Command | What happens |
|---|---|
| `https://youtube.com/watch?v=...` | Fetch transcript + generate summary |
| Any question | RAG-based Q&A from the video |
| `/start` | Welcome message |
| `/help` | All commands |
| `/summary` | Re-show current video's summary |
| `/deepdive [topic]` | Deep analysis of a topic |
| `/actionpoints` | Actionable takeaways from the video |
| `/language hi` | Switch to Hindi |
| `/language ta` | Switch to Tamil |
| `/language kn` | Switch to Kannada |
| `/language te` | Switch to Telugu |
| `/language mr` | Switch to Marathi |
| `/language en` | Switch back to English |
| `/clear` | Clear session and start fresh |

/eli5	Explain video like I'm 5
/stats	Show structured video stats card
**Language switching also works naturally:**
```
"Summarize in Hindi"
"हिंदी में बताओ"
"Explain in Tamil"
"ಕನ್ನಡದಲ್ಲಿ ಹೇಳಿ"
```

---

## How It Works — Architecture

```
User (Telegram)
      │
      ▼
 OpenClaw Agent
  ├─ SOUL.md        → Bot identity, behaviour rules, command mapping
  └─ SKILL.md       → Step-by-step workflow instructions
       │
       │  Calls Python scripts via shell
       ▼
┌─────────────────────────────────────────────────────────┐
│  skills/youtube-summarizer/scripts/                     │
│                                                         │
│  fetch_transcript.py  → youtube-transcript-api          │
│          ↓                     ↓                        │
│          └──────────────> diskcache (7-day cache)       │
│                                                         │
│  process_video.py     → langchain chunker               │
│          ↓               → all-MiniLM-L6-v2 (CPU)      │
│          └──────────────> ChromaDB (disk-persisted)     │
│                                                         │
│  generate_summary.py  → ChromaDB top chunks             │
│          ↓               → Gemini 2.0 Flash API         │
│          └──────────────> diskcache (summary cache)     │
│                                                         │
│  answer_question.py   → embed query (MiniLM)            │
│          ↓               → ChromaDB RAG (top 5 chunks)  │
│          └──────────────> Gemini 2.0 Flash API          │
│                                                         │
│  manage_session.py    → SQLite (per-user state)         │
└─────────────────────────────────────────────────────────┘
```

### How OpenClaw Drives This

OpenClaw reads two files:
- **`SOUL.md`** — tells the agent who it is, what to do for each message type, and which scripts to run in what order
- **`skills/youtube-summarizer/SKILL.md`** — detailed step-by-step instructions with exact CLI commands

When a user sends a message, OpenClaw's LLM reads these instructions and executes the Python scripts via shell. The scripts output JSON which OpenClaw parses and responds with.

---

## Technology Choices

### Why Gemini API instead of local LLM?
| | Gemini 2.0 Flash | Local Ollama |
|---|---|---|
| GPU needed? | ❌ No | ✅ Practical need |
| Speed on CPU | Fast (network) | Very slow (30s+) |
| Multilingual | Native, excellent | Varies |
| Cost | Free tier | Free |
| **Decision** | ✅ Chosen | Too slow |

### Why youtube-transcript-api instead of Whisper?
Whisper requires a GPU for acceptable speed. `youtube-transcript-api` fetches existing YouTube captions in ~1 second. The trade-off: it only works on videos with captions (most English content has auto-captions).

### Why ChromaDB?
Zero setup, runs from pip, persists to disk automatically, CPU-native. No Redis, no Postgres, no docker.

### Why all-MiniLM-L6-v2?
80MB model, ~50ms per batch on CPU. Free, offline, good quality for retrieval tasks. OpenAI embeddings would add cost + latency.

### Caching Strategy
```
Tier 1: diskcache — raw transcript        (7-day TTL)
Tier 2: diskcache — generated summaries   (7-day TTL, per language)
Tier 3: ChromaDB  — embeddings            (permanent)
```
On repeat requests for the same video, only Q&A calls Gemini. Everything else is served from cache.

---

## Project Structure

```
ytSumBot/
├── SOUL.md                              # OpenClaw bot personality + command routing
├── .env.example                         # Env template
├── .env                                 # Your secrets (gitignored)
├── requirements.txt
├── README.md
├── .gitignore
│
├── skills/
│   └── youtube-summarizer/
│       ├── SKILL.md                     # OpenClaw skill — step-by-step workflows
│       └── scripts/
│           ├── utils.py                 # Shared config + clients + helpers
│           ├── manage_session.py        # SQLite CRUD for sessions + chat history
│           ├── fetch_transcript.py      # Validate URL, fetch & cache transcript
│           ├── process_video.py         # Chunk + embed + store in ChromaDB
│           ├── generate_summary.py      # Structured summary via Gemini
│           └── answer_question.py       # RAG Q&A via ChromaDB + Gemini
│
└── data/                                # Created at runtime (gitignored)
    ├── chroma/                          # Vector embeddings (ChromaDB)
    ├── cache/                           # Transcripts + summaries (diskcache)
    └── sessions/                        # User sessions (SQLite)
```

---

## Test Scripts Directly (Debugging)

```bash
source venv/bin/activate
cd ytSumBot/

# 1. Fetch a transcript
python3 skills/youtube-summarizer/scripts/fetch_transcript.py \
  "https://www.youtube.com/watch?v=dQw4w9WgXcQ" "testuser"

# 2. Process it (chunk + embed)
python3 skills/youtube-summarizer/scripts/process_video.py \
  "dQw4w9WgXcQ" "testuser"

# 3. Set session
python3 skills/youtube-summarizer/scripts/manage_session.py \
  set "testuser" video_id "dQw4w9WgXcQ"

# 4. Generate summary
python3 skills/youtube-summarizer/scripts/generate_summary.py \
  "dQw4w9WgXcQ" "testuser" "en"

# 5. Ask a question
python3 skills/youtube-summarizer/scripts/answer_question.py \
  "What is the main topic?" "testuser" "en"

# 6. Test Hindi summary
python3 skills/youtube-summarizer/scripts/generate_summary.py \
  "dQw4w9WgXcQ" "testuser" "hi"
```

---

## Troubleshooting

**`npm error 404 '@openclaw/cli' is not in this registry`**
→ Wrong package name. Use: `npm install -g openclaw@latest` (no `@openclaw/`)

**`node: command not found` or wrong version**
→ OpenClaw needs Node 22+. Check: `node --version`

**`No captions available`**
→ The video doesn't have YouTube captions. Try an educational/tech video — most have auto-captions.

**`GEMINI_API_KEY not set`**
→ Make sure `.env` is in the project root folder (same level as `SOUL.md`)

**Slow first response**
→ First run downloads the MiniLM embedding model (~80MB). Subsequent runs are fast.

**ChromaDB error on restart**
→ Delete `data/chroma/` and send the YouTube link again — it will re-embed.

**OpenClaw not finding skills**
→ Make sure you run `openclaw onboard` from the project directory, or set your workspace path to the `ytSumBot/` folder.

---

## Edge Cases Handled

| Situation | Response |
|---|---| 
| Invalid URL | Clear error + example |
| Private / deleted video | `VIDEO_NOT_FOUND` message |
| No captions on video | `NO_TRANSCRIPT` message |
| Video > 3 hours | Processes best 200 chunks |
| Gemini rate limit | Retry message with timing |
| Question not in video | "This topic is not covered" |
| User has no active video | Prompt to send a URL first |
| Repeated same video | Cache hit — instant response |
| Language requested with no video | Save preference, prompt for URL |

---


FINAL 

User (Telegram)
      │
      ▼
OpenClaw Agent
  ├─ SOUL.md
  └─ SKILL.md
       │
       ▼
skills/youtube-summarizer/scripts/

fetch_transcript.py
   → youtube-transcript-api
   → diskcache (7-day cache)

process_video.py
   → 15s window segmentation
   → Cosine similarity boundary detection
   → Parent-child chunk creation
   → MiniLM embeddings (CPU)
   → ChromaDB persistence

generate_summary.py
   → Retrieve top parents
   → Gemini 2.0 Flash

answer_question.py
   → Query embedding
   → Child retrieval
   → Parent mapping
   → Confidence scoring (CRAG-lite)
   → Auto rewrite if needed
   → Gemini answer

manage_session.py
   → SQLite per-user session state

*Eywa SDE Intern Assignment | Stack: OpenClaw + Gemini 2.0 Flash + ChromaDB + SQLite + all-MiniLM-L6-v2*
