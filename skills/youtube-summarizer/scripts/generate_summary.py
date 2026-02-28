#!/usr/bin/env python3
"""
generate_summary.py — Generate a structured Telegram-ready summary using Gemini.

Usage:
  python3 generate_summary.py "<video_id>" "<user_id>" [language_code]

Output:
  {"status":"ok", "summary":"...formatted text...", "cached":true/false}
  {"status":"error", ...}
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    get_cache, get_gemini_client,
    language_instruction, LANGUAGE_NAMES,
    ok, error, seconds_to_timestamp,
)

SUMMARY_CACHE_TTL = 60 * 60 * 24 * 7  # 7 days
SUMMARY_CHUNKS    = 15                  # How many chunks to send to Gemini


def select_chunks(chunks: list) -> list:
    """
    Pick SUMMARY_CHUNKS that best cover the whole video:
    - Evenly spaced (to cover start/middle/end)
    - Plus a few of the longest (most information-dense)
    """
    if len(chunks) <= SUMMARY_CHUNKS:
        return chunks

    # Evenly spaced
    step     = len(chunks) / SUMMARY_CHUNKS
    selected = [chunks[int(i * step)] for i in range(SUMMARY_CHUNKS)]
    return selected


def build_prompt(chunk_texts: str, title: str, lang_code: str) -> str:
    lang_suffix = language_instruction(lang_code)
    return f"""You are an expert AI research assistant. Analyse the following YouTube video transcript excerpts and generate a comprehensive structured summary.

Video Title: {title}

Transcript Excerpts (with timestamps):
{chunk_texts}

Generate a summary in EXACTLY this structure. Do not deviate from this format:

---
🎥 **[Put the actual video title here, or a descriptive title based on content]**

📌 **Key Points:**
1. [Specific point — must be grounded in transcript content, not generic]
2. [Specific point]
3. [Specific point]
4. [Specific point]
5. [Specific point]

⏱ **Important Timestamps:**
- [MM:SS] — [Brief description of what is discussed here]
- [MM:SS] — [Brief description]
- [MM:SS] — [Brief description]
- [MM:SS] — [Brief description]

🧠 **Core Takeaway:**
[One powerful paragraph — the single most important insight from this video. Be specific, not generic.]

💡 **Who Should Watch This:**
[One sentence about the target audience and why this video is valuable to them.]
---

Rules:
- Base EVERYTHING strictly on the provided transcript. Do not add external knowledge.
- Key Points must be specific facts from the video — not filler like "The speaker discusses..."
- Timestamps must come from the actual [MM:SS] markers in the transcript excerpts above.
- Core Takeaway should be insightful and specific to this video's unique content.
{lang_suffix}"""


def main():
    if len(sys.argv) < 3:
        error("USAGE", "python3 generate_summary.py <video_id> <user_id> [language]")

    video_id  = sys.argv[1].strip()
    user_id   = sys.argv[2].strip()
    lang_code = (sys.argv[3].strip() if len(sys.argv) > 3 else "en") or "en"

    cache     = get_cache()
    cache_key = f"summary:{video_id}:{lang_code}"

    # ── Summary cache hit ─────────────────────────────────────────────────
    if cache_key in cache:
        ok({"summary": cache[cache_key], "cached": True})

    # ── Load chunks ───────────────────────────────────────────────────────
    chunks_key = f"chunks:{video_id}"
    if chunks_key not in cache:
        error("NO_CHUNKS", f"Video not processed yet. Run process_video.py first.")

    chunks = cache[chunks_key]["chunks"]

    # ── Get video title ───────────────────────────────────────────────────
    title = "YouTube Video"
    transcript_key = f"transcript:{video_id}"
    if transcript_key in cache:
        title = cache[transcript_key].get("title", "YouTube Video")

    # ── Build context text ────────────────────────────────────────────────
    selected = select_chunks(chunks)
    context  = "\n\n".join(
        f"[{c.get('timestamp', seconds_to_timestamp(c.get('start', 0)))}] {c['text']}"
        for c in selected
    )

    # ── Call Gemini ───────────────────────────────────────────────────────
    try:
        model   = get_gemini_client()
        prompt  = build_prompt(context, title, lang_code)
        resp    = model.generate_content(prompt)
        summary = resp.text.strip()
    except Exception as e:
        msg = str(e)
        if "429" in msg or "quota" in msg.lower() or "exhausted" in msg.lower():
            error("RATE_LIMITED", "Gemini API rate limit reached. Please try again in 30 seconds.")
        elif "400" in msg:
            error("API_ERROR", f"Gemini rejected the request: {msg}")
        else:
            error("API_ERROR", f"Gemini error: {msg}")

    # ── Cache and return ──────────────────────────────────────────────────
    cache.set(cache_key, summary, expire=SUMMARY_CACHE_TTL)
    ok({"summary": summary, "cached": False})


if __name__ == "__main__":
    main()
