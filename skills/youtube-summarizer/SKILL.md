---
name: youtube-summarizer
description: "Summarize YouTube videos and answer questions about their content. ACTIVATE when: (1) user sends any YouTube URL (youtube.com, youtu.be, shorts), (2) user asks a question after previously loading a video, (3) user runs /summary /deepdive /actionpoints, (4) user requests a language change (e.g. 'in Hindi', 'हिंदी में'). This skill handles transcript fetching, RAG-based Q&A, and multilingual summaries."
version: 1.0.0
metadata:
  openclaw:
    requires:
      env:
        - GEMINI_API_KEY
      bins:
        - python3
---

# YouTube Summarizer & Q&A — Skill Instructions

## Python Scripts (in `./skills/youtube-summarizer/scripts/`)

| Script | What it does | CLI usage |
|---|---|---|
| `fetch_transcript.py` | Validate URL, fetch & cache transcript | `python3 fetch_transcript.py "<url>" "<user_id>"` |
| `process_video.py` | Chunk text, embed with MiniLM, store in ChromaDB | `python3 process_video.py "<video_id>" "<user_id>"` |
| `generate_summary.py` | Build structured summary via Gemini | `python3 generate_summary.py "<video_id>" "<user_id>" "<lang>"` |
| `answer_question.py` | RAG Q&A via ChromaDB + Gemini | `python3 answer_question.py "<question>" "<user_id>" "<lang>"` |
| `manage_session.py` | SQLite session CRUD | `python3 manage_session.py <action> "<user_id>" [key] [value]` |

All scripts output **JSON to stdout**. Parse the `status` field first.

---

## WORKFLOW A — User sends a YouTube URL

```
Step 1: Fetch transcript
  python3 ./skills/youtube-summarizer/scripts/fetch_transcript.py "<url>" "<user_id>"

  Outputs:
    {"status":"ok",        "video_id":"...", "title":"...", "duration":123}
    {"status":"cache_hit", "video_id":"...", "title":"..."}
    {"status":"error",     "code":"INVALID_URL",    "message":"..."}
    {"status":"error",     "code":"NO_TRANSCRIPT",  "message":"..."}
    {"status":"error",     "code":"VIDEO_NOT_FOUND","message":"..."}

  → If status=error: show message to user, STOP.
  → If status=cache_hit: skip Step 2 (already embedded).

Step 2: Process video (only if NOT cache_hit)
  python3 ./skills/youtube-summarizer/scripts/process_video.py "<video_id>" "<user_id>"

  Output: {"status":"ok", "chunks_created":42, "video_id":"..."}

Step 3: Get user's language preference
  python3 ./skills/youtube-summarizer/scripts/manage_session.py get "<user_id>" language
  Output: {"value":"en"}  (or "hi", "ta", "kn", "te", "mr")
  → If value is null, use "en"

Step 4: Generate summary
  python3 ./skills/youtube-summarizer/scripts/generate_summary.py "<video_id>" "<user_id>" "<lang>"
  Output: {"status":"ok", "summary":"...formatted markdown..."}

Step 5: Save video to session
  python3 ./skills/youtube-summarizer/scripts/manage_session.py set "<user_id>" video_id "<video_id>"
  python3 ./skills/youtube-summarizer/scripts/manage_session.py set "<user_id>" video_title "<title>"

Step 6: Send the summary value directly to the user.
```

---

## WORKFLOW B — User asks a question

```
Step 1: Check for active video
  python3 ./skills/youtube-summarizer/scripts/manage_session.py get "<user_id>" video_id
  Output: {"value":"dQw4w9WgXcQ"} or {"value":null}

  → If null: tell user "👋 Please send me a YouTube link first and I'll analyze it!"
  → STOP.

Step 2: Get language
  python3 ./skills/youtube-summarizer/scripts/manage_session.py get "<user_id>" language

Step 3: Answer question
  python3 ./skills/youtube-summarizer/scripts/answer_question.py "<question>" "<user_id>" "<lang>"

  Outputs:
    {"status":"ok",        "answer":"..."}
    {"status":"not_found", "answer":"This topic is not covered in the video."}
    {"status":"no_session","answer":"..."}  → relay this message to user
    {"status":"error",     "message":"..."}

Step 4: Send the answer to the user.
```

---

## WORKFLOW C — Language change

```
Step 1: Detect language code from user's message (see SOUL.md Language Map)

Step 2: Save preference
  python3 ./skills/youtube-summarizer/scripts/manage_session.py set "<user_id>" language "<code>"

Step 3: Get active video_id
  python3 ./skills/youtube-summarizer/scripts/manage_session.py get "<user_id>" video_id

  → If null: save preference, tell user "✅ Language set to <lang>! Send a YouTube link to get started."
  → STOP.

Step 4: Regenerate summary in new language
  python3 ./skills/youtube-summarizer/scripts/generate_summary.py "<video_id>" "<user_id>" "<code>"

Step 5: Send the new summary to the user.
```

---

## WORKFLOW D — Commands

### /summary
1. `manage_session.py get <user_id> video_id`
2. If null → "👋 No active video. Send me a YouTube link!"
3. `manage_session.py get <user_id> language`
4. `generate_summary.py <video_id> <user_id> <lang>`
5. Send summary.

### /deepdive [topic]
1. Get video_id and language from session.
2. `answer_question.py "Give an in-depth analysis of this topic from the video: <topic>" <user_id> <lang>`
3. Send answer.

### /actionpoints
1. Get video_id and language from session.
2. `answer_question.py "Extract all actionable takeaways and next steps a viewer should take after watching this video" <user_id> <lang>`
3. Send answer.

### /clear
1. `manage_session.py clear <user_id>`
2. Tell user: "✅ Session cleared! Send me a new YouTube link to get started."

### /start
Send:
```
👋 Welcome to YTSumBot!

I can help you:
• 🎥 Summarize any YouTube video
• 🧠 Answer questions about the video
• 🌐 Respond in Hindi, Tamil, Kannada, Telugu, or Marathi

Just send me a YouTube link to get started!
```

### /help
Send:
```
🤖 YTSumBot Commands:

📎 Send a YouTube link → Instant structured summary
❓ Ask a question → I'll answer from the video transcript

/summary → Re-show current video summary
/deepdive [topic] → Deep analysis of a specific topic
/actionpoints → Get actionable takeaways
/language [hi|ta|kn|te|mr|en] → Change response language
/clear → Clear session and start fresh
/help → Show this message
```

---

## Error Handling Reference

| Error code | Message to show user |
|---|---|
| INVALID_URL | ❌ That doesn't look like a valid YouTube link. Try: `youtube.com/watch?v=...` |
| VIDEO_NOT_FOUND | ❌ Video not found. It may be private, deleted, or region-locked. |
| NO_TRANSCRIPT | ❌ This video has no captions available. Try a video with auto-generated subtitles. |
| RATE_LIMITED | ⏳ I'm a bit busy right now. Please try again in 30 seconds. |
| API_ERROR | ⚠️ AI service temporarily unavailable. Please try again shortly. |
| CACHE_MISS | ⚠️ Something went wrong loading the video data. Please resend the YouTube link. |
