#!/usr/bin/env python3
"""
answer_question.py — RAG Q&A: embed question → ChromaDB → Gemini.

Usage:
  python3 answer_question.py "<question>" "<user_id>" [language_code]

Output:
  {"status":"ok",        "answer":"..."}
  {"status":"not_found", "answer":"This topic is not covered in the video."}
  {"status":"no_session","answer":"..."}
  {"status":"error",     "message":"..."}
"""

import sys
import json
import sqlite3
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import (
    get_chroma_collection, get_gemini_client,
    language_instruction, LANGUAGE_NAMES,
    SESSIONS_DB, ok, error,
)

SIMILARITY_THRESHOLD = 0.30  # Cosine distance threshold (1 - similarity)
TOP_K = 5


# ── Session helpers ────────────────────────────────────────────────────────
def _get_video_id(user_id: str) -> str | None:
    try:
        c = sqlite3.connect(SESSIONS_DB)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT video_id FROM sessions WHERE user_id=?", (user_id,)).fetchone()
        c.close()
        return row["video_id"] if row and row["video_id"] else None
    except Exception:
        return None


def _get_history(user_id: str) -> list:
    try:
        c = sqlite3.connect(SESSIONS_DB)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT role, message FROM chat_history WHERE user_id=? ORDER BY id DESC LIMIT 6",
            (user_id,)
        ).fetchall()
        c.close()
        return [{"role": r["role"], "message": r["message"]} for r in reversed(rows)]
    except Exception:
        return []


def _save_turn(user_id: str, role: str, message: str):
    try:
        now = datetime.utcnow().isoformat()
        c = sqlite3.connect(SESSIONS_DB)
        c.execute(
            "INSERT INTO chat_history (user_id,role,message,created_at) VALUES (?,?,?,?)",
            (user_id, role, message, now)
        )
        c.execute("""
            DELETE FROM chat_history WHERE user_id=? AND id NOT IN (
              SELECT id FROM chat_history WHERE user_id=? ORDER BY id DESC LIMIT 20
            )
        """, (user_id, user_id))
        c.commit()
        c.close()
    except Exception:
        pass


# ── Embedding ──────────────────────────────────────────────────────────────
def embed_query(text: str) -> list:
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("all-MiniLM-L6-v2").encode(text, convert_to_list=True)


# ── Retrieval ──────────────────────────────────────────────────────────────
def retrieve(video_id: str, query_vec: list) -> tuple[list, list]:
    """Returns (documents, metadatas) above the similarity threshold, sorted by timestamp."""
    collection = get_chroma_collection(video_id)
    if collection.count() == 0:
        return [], []

    n = min(TOP_K, collection.count())
    results = collection.query(
        query_embeddings=[query_vec],
        n_results=n,
        include=["documents", "metadatas", "distances"],
    )

    docs, metas, dists = (
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    )

    # ChromaDB cosine distance: 0 = identical, 2 = opposite
    # Filter: keep only relevant chunks
    filtered = [
        (d, m)
        for d, m, dist in zip(docs, metas, dists)
        if dist <= (2.0 * (1.0 - SIMILARITY_THRESHOLD))  # convert threshold to distance
    ]

    if not filtered:
        return [], []

    # Sort chronologically
    filtered.sort(key=lambda x: x[1].get("start", 0))
    return [f[0] for f in filtered], [f[1] for f in filtered]


# ── Prompt ────────────────────────────────────────────────────────────────
def build_prompt(question: str, docs: list, metas: list, history: list, lang_code: str) -> str:
    lang_suffix = language_instruction(lang_code)

    context = "\n\n".join(
        f"[{m.get('timestamp', '?:??')}] {d}"
        for d, m in zip(docs, metas)
    )

    history_str = ""
    if history:
        history_str = "\nConversation so far:\n" + "\n".join(
            f"{'User' if h['role']=='user' else 'Bot'}: {h['message']}"
            for h in history
        )

    return f"""You are a precise Q&A assistant for YouTube videos. Answer ONLY based on the video context below.

Video Transcript Excerpts:
{context}
{history_str}

User Question: {question}

Rules:
1. Answer ONLY from the transcript excerpts above. No external knowledge.
2. If clearly answered → give a concise answer and cite the timestamp (e.g., "At 3:45, ...").
3. If partially covered → share what the video does say, note the gap.
4. If not covered at all → respond ONLY with: "This topic is not covered in the video."
5. Be specific. Never be vague or generic.
{lang_suffix}"""


# ── Main ──────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 3:
        error("USAGE", 'python3 answer_question.py "<question>" <user_id> [language]')

    question  = sys.argv[1].strip()
    user_id   = sys.argv[2].strip()
    lang_code = (sys.argv[3].strip() if len(sys.argv) > 3 else "en") or "en"

    if not question:
        error("EMPTY_QUESTION", "Question cannot be empty.")

    # ── Check session ─────────────────────────────────────────────────────
    video_id = _get_video_id(user_id)
    if not video_id:
        print(json.dumps({
            "status": "no_session",
            "answer": "👋 Please send me a YouTube link first and I'll analyse it for you!",
        }))
        sys.exit(0)

    # ── Embed + retrieve ──────────────────────────────────────────────────
    try:
        query_vec = embed_query(question)
    except Exception as e:
        error("EMBED_ERROR", f"Embedding failed: {e}")

    docs, metas = retrieve(video_id, query_vec)

    # ── Nothing found ─────────────────────────────────────────────────────
    if not docs:
        not_found_msg = "This topic is not covered in the video."
        if lang_code != "en":
            try:
                model = get_gemini_client()
                lang_name = LANGUAGE_NAMES.get(lang_code, lang_code)
                resp = model.generate_content(
                    f'Translate this sentence to {lang_name} only: "{not_found_msg}"'
                )
                not_found_msg = resp.text.strip().strip('"')
            except Exception:
                pass
        print(json.dumps({"status": "not_found", "answer": not_found_msg}))
        sys.exit(0)

    # ── Generate answer ───────────────────────────────────────────────────
    history = _get_history(user_id)

    try:
        model  = get_gemini_client()
        prompt = build_prompt(question, docs, metas, history, lang_code)
        resp   = model.generate_content(prompt)
        answer = resp.text.strip()
    except Exception as e:
        msg = str(e)
        if "429" in msg or "quota" in msg.lower():
            error("RATE_LIMITED", "Gemini rate limit. Please try again in 30 seconds.")
        else:
            error("API_ERROR", f"Gemini error: {msg}")

    # ── Save to history ───────────────────────────────────────────────────
    _save_turn(user_id, "user", question)
    _save_turn(user_id, "bot",  answer)

    ok({"answer": answer})


if __name__ == "__main__":
    main()
