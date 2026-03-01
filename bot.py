#!/usr/bin/env python3
"""
bot.py v2.0 — YTSumBot Enhanced Telegram Bot

New features:
  - Smart follow-up question suggestions after summary
  - Auto language detection (script + keyword based)
  - Confidence indicator on Q&A answers (🟢🟡🔴)
  - /eli5 command (Explain Like I'm 5)
  - /stats command (video stats card)

Run: python3 bot.py
"""

import os
import re
import json
import logging
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

# ── Config ─────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SCRIPTS_DIR        = BASE_DIR / "skills/youtube-summarizer/scripts"
PYTHON             = sys.executable

if not TELEGRAM_BOT_TOKEN:
    print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
    sys.exit(1)

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── YouTube URL detection ──────────────────────────────────────────────────
YT_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/live/)"
    r"([a-zA-Z0-9_-]{11})"
)

# ── Language config ────────────────────────────────────────────────────────
LANG_KEYWORDS = {
    "hi": ["hindi", "हिंदी", "हिन्दी", "hindi mein"],
    "ta": ["tamil", "தமிழ்"],
    "kn": ["kannada", "ಕನ್ನಡ"],
    "te": ["telugu", "తెలుగు"],
    "mr": ["marathi", "मराठी"],
    "en": ["english", "in english"],
}
LANG_NAMES = {
    "en": "English", "hi": "Hindi", "ta": "Tamil",
    "kn": "Kannada",  "te": "Telugu", "mr": "Marathi",
}

# Indian script Unicode ranges
SCRIPT_RANGES = {
    "hi": (0x0900, 0x097F),  # Devanagari
    "ta": (0x0B80, 0x0BFF),  # Tamil
    "kn": (0x0C80, 0x0CFF),  # Kannada
    "te": (0x0C00, 0x0C7F),  # Telugu
}


def detect_language(text: str) -> str | None:
    """
    Auto-detect language from message using:
    1. Explicit keywords
    2. Unicode script ranges
    """
    text_lower = text.lower()

    # Keyword detection
    for code, keywords in LANG_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower or kw in text:
                return code

    # Script-based detection
    for code, (start, end) in SCRIPT_RANGES.items():
        for char in text:
            if start <= ord(char) <= end:
                return code

    return None


# ── Script runner ──────────────────────────────────────────────────────────
def run_script(script: str, *args) -> dict:
    cmd = [PYTHON, str(SCRIPTS_DIR / script)] + [str(a) for a in args]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=120, cwd=str(BASE_DIR),
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        return {"status": "error", "message": result.stderr or "No output"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "code": "TIMEOUT", "message": "Script timed out."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_session(user_id: str, key: str) -> str | None:
    result = run_script("manage_session.py", "get", user_id, key)
    return result.get("value")


def set_session(user_id: str, key: str, value: str):
    run_script("manage_session.py", "set", user_id, key, value)


# ── Error messages ─────────────────────────────────────────────────────────
ERROR_MESSAGES = {
    "INVALID_URL":    "❌ That doesn't look like a valid YouTube link.\nTry: `youtube.com/watch?v=...`",
    "VIDEO_NOT_FOUND":"❌ Video not found. It may be private, deleted, or region-locked.",
    "NO_TRANSCRIPT":  "❌ This video has no captions available.\nTry a video with auto-generated subtitles.",
    "RATE_LIMITED":   "⏳ Too many requests. Please wait 30 seconds and try again.",
    "API_ERROR":      "⚠️ AI service temporarily unavailable. Please try again shortly.",
    "CACHE_MISS":     "⚠️ Something went wrong. Please resend the YouTube link.",
    "TIMEOUT":        "⏳ Processing took too long. Please try again.",
}

def format_error(result: dict) -> str:
    code = result.get("code", "")
    return ERROR_MESSAGES.get(code, f"⚠️ {result.get('message', 'Unknown error')}")


# ── Smart follow-up suggestions ───────────────────────────────────────────
def generate_followup_suggestions(video_id: str, summary: str) -> list[str]:
    """
    Generate 3 smart follow-up questions based on the summary.
    Uses Gemini to create contextually relevant questions.
    """
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        from utils import get_gemini_client
        model = get_gemini_client()
        prompt = f"""Based on this YouTube video summary, generate exactly 3 smart follow-up questions a curious viewer would ask.

Summary:
{summary[:1000]}

Rules:
- Return ONLY a JSON array of 3 strings
- Questions must be specific to THIS video's content
- Keep each question under 60 characters
- No numbering, no explanation

Example format: ["Question 1?", "Question 2?", "Question 3?"]"""

        resp = model.generate_content(prompt)
        text = resp.text.strip()
        # Extract JSON array
        match = re.search(r'\[.*?\]', text, re.DOTALL)
        if match:
            questions = json.loads(match.group())
            return questions[:3] if isinstance(questions, list) else []
    except Exception:
        pass
    return []


def make_suggestion_keyboard(questions: list[str]) -> InlineKeyboardMarkup:
    """Create inline keyboard buttons for follow-up suggestions."""
    buttons = [
        [InlineKeyboardButton(f"❓ {q}", callback_data=f"ask:{q}")]
        for q in questions
    ]
    return InlineKeyboardMarkup(buttons)


# ── Handlers ───────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to *YTSumBot v2.0*!\n\n"
        "I can help you:\n"
        "• 🎥 Summarize any YouTube video\n"
        "• 🧠 Answer questions about the video\n"
        "• 🌐 Auto-detect your language\n"
        "• 🟢 Show answer confidence scores\n\n"
        "Just send me a YouTube link to get started!",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *YTSumBot Commands:*\n\n"
        "📎 Send a YouTube link → Instant structured summary\n"
        "❓ Ask a question → RAG-powered answer with confidence score\n\n"
        "/summary → Re-show current video summary\n"
        "/deepdive \\[topic\\] → Deep analysis of a topic\n"
        "/actionpoints → Get actionable takeaways\n"
        "/eli5 → Explain the video like I'm 5\n"
        "/stats → Video stats card\n"
        "/language hi|ta|kn|te|mr|en → Change language\n"
        "/clear → Clear session\n"
        "/help → Show this message\n\n"
        "💡 *Tip:* Type in Hindi/Tamil/Kannada and I'll auto-detect!",
        parse_mode="Markdown",
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    run_script("manage_session.py", "clear", user_id)
    await update.message.reply_text("✅ Session cleared! Send me a new YouTube link to get started.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = str(update.effective_user.id)
    video_id = get_session(user_id, "video_id")
    if not video_id:
        await update.message.reply_text("👋 No active video. Send me a YouTube link first!")
        return

    cache_path = BASE_DIR / "data/cache"
    try:
        import diskcache
        cache = diskcache.Cache(str(cache_path))
        data  = cache.get(f"transcript:{video_id}", {})
        duration = data.get("duration", 0)
        mins  = int(duration // 60)
        secs  = int(duration % 60)
        segs  = len(data.get("segments", []))

        from utils import get_chroma_collection
        sys.path.insert(0, str(SCRIPTS_DIR))
        collection = get_chroma_collection(video_id)
        chunks = collection.count()

        await update.message.reply_text(
            f"📊 *Video Stats*\n\n"
            f"🎬 Video ID: `{video_id}`\n"
            f"⏱ Duration: {mins}m {secs}s\n"
            f"📝 Transcript segments: {segs}\n"
            f"🧩 Chunks in vector store: {chunks}\n"
            f"🌐 Language: {LANG_NAMES.get(get_session(user_id, 'language') or 'en', 'English')}",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch stats: {e}")


async def eli5_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = str(update.effective_user.id)
    video_id = get_session(user_id, "video_id")
    if not video_id:
        await update.message.reply_text("👋 No active video. Send me a YouTube link first!")
        return
    lang = get_session(user_id, "language") or "en"
    await update.message.reply_text("👶 Explaining like you're 5...")
    result = run_script(
        "answer_question.py",
        "Explain the main concept of this video in the simplest possible way, as if explaining to a 5-year-old child. Use simple words, fun analogies, and avoid jargon.",
        user_id, lang
    )
    if result.get("status") in ("ok", "not_found"):
        await update.message.reply_text(f"👶 *ELI5 Explanation:*\n\n{result['answer']}", parse_mode="Markdown")
    else:
        await update.message.reply_text(format_error(result))


async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = str(update.effective_user.id)
    video_id = get_session(user_id, "video_id")
    if not video_id:
        await update.message.reply_text("👋 No active video. Send me a YouTube link first!")
        return
    lang = get_session(user_id, "language") or "en"
    await update.message.reply_text("⏳ Fetching summary...")
    result = run_script("generate_summary.py", video_id, user_id, lang)
    if result.get("status") == "ok":
        await update.message.reply_text(result["summary"])
    else:
        await update.message.reply_text(format_error(result))


async def deepdive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = str(update.effective_user.id)
    video_id = get_session(user_id, "video_id")
    if not video_id:
        await update.message.reply_text("👋 No active video. Send me a YouTube link first!")
        return
    topic = " ".join(context.args) if context.args else ""
    if not topic:
        await update.message.reply_text("Usage: /deepdive [topic]\nExample: /deepdive sigmoid function")
        return
    lang = get_session(user_id, "language") or "en"
    await update.message.reply_text(f"🔍 Deep diving into: *{topic}*...", parse_mode="Markdown")
    result = run_script("answer_question.py", f"Give an in-depth analysis of: {topic}", user_id, lang)
    if result.get("status") in ("ok", "not_found"):
        await update.message.reply_text(result["answer"])
    else:
        await update.message.reply_text(format_error(result))


async def actionpoints_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id  = str(update.effective_user.id)
    video_id = get_session(user_id, "video_id")
    if not video_id:
        await update.message.reply_text("👋 No active video. Send me a YouTube link first!")
        return
    lang = get_session(user_id, "language") or "en"
    await update.message.reply_text("📋 Extracting action points...")
    result = run_script(
        "answer_question.py",
        "List all actionable takeaways and next steps a viewer should take after watching this video",
        user_id, lang
    )
    if result.get("status") in ("ok", "not_found"):
        await update.message.reply_text(result["answer"])
    else:
        await update.message.reply_text(format_error(result))


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang    = context.args[0].lower() if context.args else "en"
    if lang not in LANG_NAMES:
        await update.message.reply_text("❌ Supported: en, hi, ta, kn, te, mr\nExample: /language hi")
        return
    set_session(user_id, "language", lang)
    video_id = get_session(user_id, "video_id")
    if not video_id:
        await update.message.reply_text(f"✅ Language set to {LANG_NAMES[lang]}! Send a YouTube link to get started.")
        return
    await update.message.reply_text(f"🌐 Switching to {LANG_NAMES[lang]}...")
    result = run_script("generate_summary.py", video_id, user_id, lang)
    if result.get("status") == "ok":
        await update.message.reply_text(result["summary"])
    else:
        await update.message.reply_text(format_error(result))


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button presses (follow-up question suggestions)."""
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("ask:"):
        question = data[4:]
        user_id  = str(query.from_user.id)
        lang     = get_session(user_id, "language") or "en"

        await query.message.reply_text(f"❓ *{question}*", parse_mode="Markdown")
        await query.message.reply_text("🔍 Searching the video...")

        result = run_script("answer_question.py", question, user_id, lang)
        if result.get("status") in ("ok", "not_found"):
            await query.message.reply_text(result["answer"])
        else:
            await query.message.reply_text(format_error(result))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text    = update.message.text.strip()

    # ── Auto language detection ────────────────────────────────────────────
    detected_lang = detect_language(text)

    # If it's a short language-only message (not a question)
    if detected_lang and len(text.split()) <= 6 and not YT_PATTERN.search(text):
        set_session(user_id, "language", detected_lang)
        video_id = get_session(user_id, "video_id")
        if not video_id:
            await update.message.reply_text(
                f"✅ Language set to {LANG_NAMES[detected_lang]}! Send a YouTube link to get started."
            )
            return
        await update.message.reply_text(f"🌐 Switching to {LANG_NAMES[detected_lang]}...")
        result = run_script("generate_summary.py", video_id, user_id, detected_lang)
        if result.get("status") == "ok":
            await update.message.reply_text(result["summary"])
        else:
            await update.message.reply_text(format_error(result))
        return

    # ── YouTube URL ────────────────────────────────────────────────────────
    yt_match = YT_PATTERN.search(text)
    if yt_match:
        await update.message.reply_text("⏳ Fetching transcript...")

        fetch_result = run_script("fetch_transcript.py", text, user_id)
        if fetch_result.get("status") == "error":
            await update.message.reply_text(format_error(fetch_result))
            return

        video_id = fetch_result["video_id"]

        if fetch_result.get("status") != "cache_hit":
            await update.message.reply_text("🧠 Processing video with topic-aware segmentation...")
            process_result = run_script("process_video.py", video_id, user_id)
            if process_result.get("status") == "error":
                await update.message.reply_text(format_error(process_result))
                return
            topics = process_result.get("topics_detected", 0)
            if topics:
                await update.message.reply_text(f"🗂 Detected *{topics} topic segments* in this video", parse_mode="Markdown")

        set_session(user_id, "video_id", video_id)
        set_session(user_id, "video_title", fetch_result.get("title", "YouTube Video"))

        lang = get_session(user_id, "language") or "en"
        await update.message.reply_text("✍️ Generating summary...")
        summary_result = run_script("generate_summary.py", video_id, user_id, lang)

        if summary_result.get("status") == "ok":
            summary = summary_result["summary"]
            await update.message.reply_text(summary)

            # ── Smart follow-up suggestions ────────────────────────────
            await update.message.reply_text("💡 Generating smart follow-up questions...")
            suggestions = generate_followup_suggestions(video_id, summary)
            if suggestions:
                keyboard = make_suggestion_keyboard(suggestions)
                await update.message.reply_text(
                    "💬 *Suggested questions — tap to ask:*",
                    reply_markup=keyboard,
                    parse_mode="Markdown",
                )
            else:
                await update.message.reply_text(
                    "💬 Feel free to ask me anything about this video!\n"
                    "Or try /deepdive, /actionpoints, /eli5"
                )
        else:
            await update.message.reply_text(format_error(summary_result))
        return

    # ── Question (with auto language detection) ────────────────────────────
    video_id = get_session(user_id, "video_id")
    if not video_id:
        await update.message.reply_text(
            "👋 Please send me a YouTube link first and I'll analyse it for you!"
        )
        return

    # Auto-detect language from question (if user writes in Hindi etc.)
    if detected_lang and detected_lang != "en":
        set_session(user_id, "language", detected_lang)
        lang = detected_lang
    else:
        lang = get_session(user_id, "language") or "en"

    await update.message.reply_text("🔍 Searching the video...")
    result = run_script("answer_question.py", text, user_id, lang)

    if result.get("status") in ("ok", "not_found"):
        await update.message.reply_text(result["answer"])
    elif result.get("status") == "no_session":
        await update.message.reply_text(result["answer"])
    else:
        await update.message.reply_text(format_error(result))


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    print("🦞 YTSumBot v2.0 starting...")
    print(f"   Scripts: {SCRIPTS_DIR}")
    print(f"   Python:  {PYTHON}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",        start))
    app.add_handler(CommandHandler("help",         help_command))
    app.add_handler(CommandHandler("clear",        clear_command))
    app.add_handler(CommandHandler("summary",      summary_command))
    app.add_handler(CommandHandler("deepdive",     deepdive_command))
    app.add_handler(CommandHandler("actionpoints", actionpoints_command))
    app.add_handler(CommandHandler("language",     language_command))
    app.add_handler(CommandHandler("eli5",         eli5_command))
    app.add_handler(CommandHandler("stats",        stats_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot is running! Open Telegram and send a YouTube link.")
    print("   Press Ctrl+C to stop.\n")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
