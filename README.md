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

## Setup — Step by Step

### Step 1 — Install Node 22 (if not already)

**macOS:**
```bash
brew install node@22
# or use nvm:
nvm install 22 && nvm use 22
```

**Windows (WSL2 recommended):**
```bash
nvm install 22
nvm use 22
```

**Linux:**
```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs
```

Verify: `node --version`  → should show `v22.x.x`

---

### Step 2 — Install OpenClaw

```bash
# The correct package name is `openclaw` (not @openclaw/cli)
npm install -g openclaw@latest

# Verify
openclaw --version
```

---

### Step 3 — Clone this project

```bash
git clone https://github.com/yourname/ytSumBot
cd ytSumBot
```

---

### Step 4 — Install Python dependencies

```bash
# Create virtual environment (keeps packages isolated)
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# Install all packages
pip install -r requirements.txt
```

Pre-download the embedding model (80MB, one-time):
```bash
python3 -c "
from sentence_transformers import SentenceTransformer
print('Downloading all-MiniLM-L6-v2...')
SentenceTransformer('all-MiniLM-L6-v2')
print('Done!')
"
```

---

### Step 5 — Configure your API key

```bash
cp .env.example .env
```

Open `.env` and add your Gemini API key:
```
GEMINI_API_KEY=AIzaSy...your_key_here...
```

Get a free Gemini API key at: https://aistudio.google.com/app/apikey

---

### Step 6 — Create data directories

```bash
mkdir -p data/{chroma,cache,sessions}
```

---

### Step 7 — Run the OpenClaw onboarding wizard

This is the one-time setup that connects OpenClaw to Telegram and configures your workspace.

```bash
# From your project directory (ytSumBot/)
openclaw onboard
```

The wizard will ask you:

1. **Which AI provider?** → Choose `Google Gemini`
2. **Gemini API key?** → Paste from aistudio.google.com
3. **Which model?** → `gemini-2.0-flash`
4. **Telegram bot token?** → Paste from @BotFather (see below)
5. **Workspace directory?** → Enter the full path to your `ytSumBot/` folder

**How to get your Telegram Bot Token:**
1. Open Telegram → search `@BotFather`
2. Send `/newbot`
3. Choose a name: e.g., `YTSumBot`
4. Choose a username: e.g., `@myYTSumBot`
5. BotFather replies with your token: `7123456789:AAHxxxxxxxxxxxxxxxx`

---

### Step 8 — Start the bot

```bash
openclaw start
```

You'll see output like:
```
✅ OpenClaw started
✅ Workspace: /path/to/ytSumBot
✅ Skill loaded: youtube-summarizer
✅ Telegram channel connected (@myYTSumBot)
🦞 Agent is ready
```

---

### Step 9 — Test it!

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

*Eywa SDE Intern Assignment | Stack: OpenClaw + Gemini 2.0 Flash + ChromaDB + SQLite + all-MiniLM-L6-v2*
