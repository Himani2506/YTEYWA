#!/usr/bin/env python3
"""
bot.py — Standalone Telegram bot for YTSumBot.
Directly calls Python scripts. No OpenClaw needed.
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
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters,
)

# ── Config ─────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
SCRIPTS_DIR        = BASE_DIR / "skills/youtube-summarizer/scripts"
PYTHON             = sys.executable  # uses current venv python

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

# ── Language detection ─────────────────────────────────────────────────────
LANG_KEYWORDS = {
    "hi": ["hindi", "हिंदी", "हिन्दी"],
    "ta": ["tamil", "தமிழ்"],
    "kn": ["kannada", "ಕನ್ನಡ"],
    "te": ["telugu", "తెలుగు"],
    "mr": ["marathi", "मराठी"],
    "en": ["english"],
}
LANG_NAMES = {
    "en": "English", "hi": "Hindi", "ta": "Tamil",
    "kn": "Kannada", "te": "Telugu", "mr": "Marathi",
}

def detect_language(text: str) -> str | None:
    text_lower = text.lower()
    for code, keywords in LANG_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower or kw in text:
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
        # Parse first valid JSON line from stdout
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        return {"status": "error", "message": result.stderr or "No output from script"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "Script timed out. Please try again."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def get_session(user_id: str, key: str) -> str | None:
    result = run_script("manage_session.py", "get", user_id, key)
    return result.get("value")


def set_session(user_id: str, key: str, value: str):
    run_script("manage_session.py", "set", user_id, key, value)


# ── Error message map ──────────────────────────────────────────────────────
ERROR_MESSAGES = {
    "INVALID_URL":    "❌ That doesn't look like a valid YouTube link.\nTry: `youtube.com/watch?v=...`",
    "VIDEO_NOT_FOUND":"❌ Video not found. It may be private, deleted, or region-locked.",
    "NO_TRANSCRIPT":  "❌ This video has no captions available.\nTry a video with auto-generated subtitles.",
    "RATE_LIMITED":   "⏳ Too many requests. Please wait 30 seconds and try again.",
    "API_ERROR":      "⚠️ AI service temporarily unavailable. Please try again shortly.",
    "CACHE_MISS":     "⚠️ Something went wrong. Please resend the YouTube link.",
}

def format_error(result: dict) -> str:
    code = result.get("code", "")
    return ERROR_MESSAGES.get(code, f"⚠️ {result.get('message', 'Unknown error')}")


# ── Handlers ───────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to *YTSumBot*!\n\n"
        "I can help you:\n"
        "• 🎥 Summarize any YouTube video\n"
        "• 🧠 Answer questions about the video\n"
        "• 🌐 Respond in Hindi, Tamil, Kannada, Telugu, or Marathi\n\n"
        "Just send me a YouTube link to get started!",
        parse_mode="Markdown",
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *YTSumBot Commands:*\n\n"
        "📎 Send a YouTube link → Instant structured summary\n"
        "❓ Ask a question → I'll answer from the video\n\n"
        "/summary → Re-show current video summary\n"
        "/deepdive \\[topic\\] → Deep analysis of a topic\n"
        "/actionpoints → Get actionable takeaways\n"
        "/language hi|ta|kn|te|mr|en → Change language\n"
        "/clear → Clear session\n"
        "/help → Show this message",
        parse_mode="Markdown",
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    run_script("manage_session.py", "clear", user_id)
    await update.message.reply_text("✅ Session cleared! Send me a new YouTube link to get started.")


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
        await update.message.reply_text("Usage: /deepdive [topic]\nExample: /deepdive neural networks")
        return
    lang   = get_session(user_id, "language") or "en"
    await update.message.reply_text(f"🔍 Deep diving into: *{topic}*...", parse_mode="Markdown")
    result = run_script("answer_question.py", f"Give an in-depth analysis of this topic from the video: {topic}", user_id, lang)
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
    lang   = get_session(user_id, "language") or "en"
    await update.message.reply_text("📋 Extracting action points...")
    result = run_script("answer_question.py", "List all actionable takeaways and next steps a viewer should take after watching this video", user_id, lang)
    if result.get("status") in ("ok", "not_found"):
        await update.message.reply_text(result["answer"])
    else:
        await update.message.reply_text(format_error(result))


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    lang    = context.args[0].lower() if context.args else "en"
    if lang not in LANG_NAMES:
        await update.message.reply_text(
            "❌ Supported languages: en, hi, ta, kn, te, mr\n"
            "Example: /language hi"
        )
        return
    set_session(user_id, "language", lang)
    video_id = get_session(user_id, "video_id")
    if not video_id:
        await update.message.reply_text(f"✅ Language set to {LANG_NAMES[lang]}! Send a YouTube link to get started.")
        return
    await update.message.reply_text(f"✅ Language set to {LANG_NAMES[lang]}! Regenerating summary...")
    result = run_script("generate_summary.py", video_id, user_id, lang)
    if result.get("status") == "ok":
        await update.message.reply_text(result["summary"])
    else:
        await update.message.reply_text(format_error(result))


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text    = update.message.text.strip()

    # ── Language change request ────────────────────────────────────────────
    lang_detected = detect_language(text)
    if lang_detected and len(text) < 50:  # short message = language command
        set_session(user_id, "language", lang_detected)
        video_id = get_session(user_id, "video_id")
        if not video_id:
            await update.message.reply_text(
                f"✅ Language set to {LANG_NAMES[lang_detected]}! Send a YouTube link to get started."
            )
            return
        await update.message.reply_text(f"🌐 Switching to {LANG_NAMES[lang_detected]}...")
        result = run_script("generate_summary.py", video_id, user_id, lang_detected)
        if result.get("status") == "ok":
            await update.message.reply_text(result["summary"])
        else:
            await update.message.reply_text(format_error(result))
        return

    # ── YouTube URL ────────────────────────────────────────────────────────
    yt_match = YT_PATTERN.search(text)
    if yt_match:
        await update.message.reply_text("⏳ Fetching transcript...")

        # Step 1: Fetch transcript
        fetch_result = run_script("fetch_transcript.py", text, user_id)
        if fetch_result.get("status") == "error":
            await update.message.reply_text(format_error(fetch_result))
            return

        video_id = fetch_result["video_id"]

        # Step 2: Process (chunk + embed) if not cache hit
        if fetch_result.get("status") != "cache_hit":
            await update.message.reply_text("🧠 Processing video...")
            process_result = run_script("process_video.py", video_id, user_id)
            if process_result.get("status") == "error":
                await update.message.reply_text(format_error(process_result))
                return

        # Step 3: Save session
        set_session(user_id, "video_id", video_id)
        set_session(user_id, "video_title", fetch_result.get("title", "YouTube Video"))

        # Step 4: Generate summary
        lang = get_session(user_id, "language") or "en"
        await update.message.reply_text("✍️ Generating summary...")
        summary_result = run_script("generate_summary.py", video_id, user_id, lang)
        if summary_result.get("status") == "ok":
            await update.message.reply_text(summary_result["summary"])
            await update.message.reply_text(
                "💬 Feel free to ask me anything about this video!\n"
                "Or try /deepdive, /actionpoints, or /language hi"
            )
        else:
            await update.message.reply_text(format_error(summary_result))
        return

    # ── Question ───────────────────────────────────────────────────────────
    video_id = get_session(user_id, "video_id")
    if not video_id:
        await update.message.reply_text(
            "👋 Please send me a YouTube link first and I'll analyse it for you!"
        )
        return

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
    print("🦞 YTSumBot starting...")
    print(f"   Scripts dir: {SCRIPTS_DIR}")
    print(f"   Python: {PYTHON}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",        start))
    app.add_handler(CommandHandler("help",         help_command))
    app.add_handler(CommandHandler("clear",        clear_command))
    app.add_handler(CommandHandler("summary",      summary_command))
    app.add_handler(CommandHandler("deepdive",     deepdive_command))
    app.add_handler(CommandHandler("actionpoints", actionpoints_command))
    app.add_handler(CommandHandler("language",     language_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("✅ Bot is running! Open Telegram and send a YouTube link.")
    print("   Press Ctrl+C to stop.\n")

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
