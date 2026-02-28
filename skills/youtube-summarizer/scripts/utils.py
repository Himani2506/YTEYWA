"""
utils.py — Shared utilities for YTSumBot scripts.
"""
import os
import re
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

# ── Load .env from project root ────────────────────────────────────────────
def _find_env():
    p = Path(__file__).resolve()
    for _ in range(6):
        candidate = p / ".env"
        if candidate.exists():
            return str(candidate)
        p = p.parent
    return None

_env_path = _find_env()
if _env_path:
    load_dotenv(_env_path)

# ── Paths ──────────────────────────────────────────────────────────────────
# Walk up to find project root (has .env or data/ directory)
def _find_project_root():
    p = Path(__file__).resolve().parent
    for _ in range(6):
        if (p / ".env").exists() or (p / "SOUL.md").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    # Fallback: 3 levels up from scripts/ → project root
    return Path(__file__).resolve().parents[3]

_ROOT = _find_project_root()

CHROMA_PATH   = os.getenv("CHROMA_PATH",  str(_ROOT / "data/chroma"))
CACHE_PATH    = os.getenv("CACHE_PATH",   str(_ROOT / "data/cache"))
SESSIONS_DB   = os.getenv("SESSIONS_DB",  str(_ROOT / "data/sessions/sessions.db"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Ensure data directories exist
for _p in [CHROMA_PATH, CACHE_PATH, str(Path(SESSIONS_DB).parent)]:
    Path(_p).mkdir(parents=True, exist_ok=True)


# ── JSON output helpers ────────────────────────────────────────────────────
def ok(data: dict):
    """Print JSON success and exit 0."""
    print(json.dumps({"status": "ok", **data}))
    sys.exit(0)

def error(code: str, message: str):
    """Print JSON error and exit 1."""
    print(json.dumps({"status": "error", "code": code, "message": message}))
    sys.exit(1)

def out(data: dict):
    """Print raw JSON dict and exit 0."""
    print(json.dumps(data))
    sys.exit(0)


# ── YouTube URL parsing ────────────────────────────────────────────────────
_YT_PATTERNS = [
    r"(?:https?://)?(?:www\.)?youtube\.com/watch\?(?:.*&)?v=([a-zA-Z0-9_-]{11})",
    r"(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})",
    r"(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})",
    r"(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})",
    r"(?:https?://)?(?:www\.)?youtube\.com/live/([a-zA-Z0-9_-]{11})",
]

def extract_video_id(url: str) -> str | None:
    url = url.strip()
    for pattern in _YT_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


# ── Gemini client ──────────────────────────────────────────────────────────
def get_gemini_client():
    if not GEMINI_API_KEY:
        error("CONFIG_ERROR", "GEMINI_API_KEY not set. Add it to your .env file.")
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        generation_config={
            "temperature":      0.3,
            "max_output_tokens": 4096,
        },
    )


# ── ChromaDB ───────────────────────────────────────────────────────────────
def get_chroma_collection(video_id: str):
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection(
        name=f"yt-{video_id}",
        metadata={"hnsw:space": "cosine"},
    )


# ── diskcache ──────────────────────────────────────────────────────────────
def get_cache():
    import diskcache
    return diskcache.Cache(CACHE_PATH)


# ── Language helpers ───────────────────────────────────────────────────────
LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "kn": "Kannada",
    "te": "Telugu",
    "mr": "Marathi",
}

def language_instruction(lang_code: str) -> str:
    if lang_code == "en" or lang_code not in LANGUAGE_NAMES:
        return ""
    name = LANGUAGE_NAMES[lang_code]
    return (
        f"\n\nCRITICAL LANGUAGE INSTRUCTION: You MUST respond entirely in {name}. "
        f"Every single word in your response must be in {name}. "
        f"Do not mix in any English except for proper nouns, YouTube titles, or timestamps."
    )


# ── Timestamp ─────────────────────────────────────────────────────────────
def seconds_to_timestamp(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
