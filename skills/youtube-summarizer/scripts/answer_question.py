#!/usr/bin/env python3
"""
answer_question.py v2.0 — CRAG-lite + Parent-Child Retrieval

Upgrades:
  3. CRAG-lite (Corrective RAG):
     - Scores retrieved chunks by similarity
     - If all scores are low → rewrites query via Gemini → retries
     - Self-correcting: tries harder before saying "not found"
     - Reports confidence: 🟢 High / 🟡 Medium / 🔴 Low

  + Parent-Child Retrieval:
     - Retrieves using small child chunks (precise)
     - But sends PARENT chunks (rich context) to Gemini
     - Deduplicates parents so Gemini sees clean context

Usage:
  python3 answer_question.py "<question>" "<user_id>" [language_code]
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

# ── Thresholds ─────────────────────────────────────────────────────────────
TOP_K                  = 8     # Retrieve more children, dedupe to fewer parents
HIGH_CONFIDENCE_DIST   = 0.35  # ChromaDB distance (lower = more similar)
MEDIUM_CONFIDENCE_DIST = 0.55
# Above MEDIUM = low confidence → trigger CRAG correction

MAX_CRAG_RETRIES = 1   # One query rewrite attempt


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


# ── Retrieval (Child → Parent) ─────────────────────────────────────────────
def retrieve(video_id: str, query_vec: list) -> tuple[list[str], list[dict], list[float]]:
    """
    Query ChromaDB using child embeddings.
    Return PARENT texts (deduplicated), their metadata, and distances.
    """
    collection = get_chroma_collection(video_id)
    if collection.count() == 0:
        return [], [], []

    n = min(TOP_K, collection.count())
    results = collection.query(
        query_embeddings=[query_vec],
        n_results=n,
        include=["documents", "metadatas", "distances"],
    )

    child_docs = results["documents"][0]
    metas      = results["metadatas"][0]
    distances  = results["distances"][0]

    # Deduplicate: use parent_text from metadata if available
    # This is the Parent-Child retrieval magic
    seen_parents = set()
    parent_docs  = []
    parent_metas = []
    parent_dists = []

    for doc, meta, dist in zip(child_docs, metas, distances):
        # Use parent_text if stored (new pipeline), else use child doc
        parent_text = meta.get("parent_text", doc)
        parent_key  = parent_text[:80]  # dedup key

        if parent_key not in seen_parents:
            seen_parents.add(parent_key)
            parent_docs.append(parent_text)
            parent_metas.append(meta)
            parent_dists.append(dist)

    # Sort by distance (best first)
    paired = sorted(
        zip(parent_docs, parent_metas, parent_dists),
        key=lambda x: x[2]
    )
    if paired:
        parent_docs, parent_metas, parent_dists = zip(*paired)
        return list(parent_docs), list(parent_metas), list(parent_dists)

    return [], [], []


# ── Confidence scoring ─────────────────────────────────────────────────────
def score_confidence(distances: list[float]) -> tuple[str, str]:
    """
    Convert ChromaDB distances to confidence level.
    Returns (level, emoji_label)
    """
    if not distances:
        return "none", "🔴 Low confidence"

    best = min(distances)

    if best <= HIGH_CONFIDENCE_DIST:
        return "high",   "🟢 High confidence"
    elif best <= MEDIUM_CONFIDENCE_DIST:
        return "medium", "🟡 Medium confidence"
    else:
        return "low",    "🔴 Low confidence"


# ── CRAG: Query rewriter ───────────────────────────────────────────────────
def rewrite_query(original_question: str, model) -> str:
    """
    Use Gemini to rephrase the question for better retrieval.
    This is the heart of CRAG-lite.
    """
    prompt = f"""You are a search query optimizer. 
    
The following question will be used to search a YouTube video transcript.
Rewrite it to be more specific and keyword-rich for better retrieval.
Return ONLY the rewritten query. Nothing else. No explanation.

Original question: {original_question}

Rewritten query:"""

    try:
        resp = model.generate_content(prompt)
        rewritten = resp.text.strip().strip('"').strip("'")
        return rewritten if rewritten else original_question
    except Exception:
        return original_question


# ── Main Q&A prompt ────────────────────────────────────────────────────────
def build_qa_prompt(
    question: str,
    docs: list, metas: list,
    history: list,
    lang_code: str,
    is_corrected: bool = False,
) -> str:
    lang_suffix = language_instruction(lang_code)

    context = "\n\n".join(
        f"[{m.get('timestamp', m.get('parent_ts', '?:??'))}] {d}"
        for d, m in zip(docs, metas)
    )

    history_str = ""
    if history:
        history_str = "\nConversation so far:\n" + "\n".join(
            f"{'User' if h['role']=='user' else 'Bot'}: {h['message']}"
            for h in history
        )

    correction_note = ""
    if is_corrected:
        correction_note = "\n(Note: Query was reformulated to find the best matching content.)"

    return f"""You are a precise Q&A assistant for YouTube videos. Answer ONLY based on the video context below.

Video Transcript Context (with timestamps):
{context}
{history_str}{correction_note}

User Question: {question}

Rules:
1. Answer ONLY from the context above. No external knowledge.
2. Cite the timestamp when relevant (e.g. "At 3:45, ...").
3. If the answer is not in the context, respond ONLY with: "This topic is not covered in the video."
4. Be specific and concise.
{lang_suffix}"""


# ── Main ───────────────────────────────────────────────────────────────────
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

    # ── Embed original question ───────────────────────────────────────────
    try:
        query_vec = embed_query(question)
    except Exception as e:
        error("EMBED_ERROR", f"Embedding failed: {e}")

    # ── Round 1: Initial retrieval ────────────────────────────────────────
    docs, metas, distances = retrieve(video_id, query_vec)
    confidence_level, confidence_label = score_confidence(distances)
    is_corrected = False

    # ── CRAG-lite: Corrective retrieval ───────────────────────────────────
    if confidence_level == "low" and docs:
        # Low confidence → rewrite query and retry
        try:
            model = get_gemini_client()
            rewritten = rewrite_query(question, model)

            if rewritten != question:
                rewritten_vec = embed_query(rewritten)
                new_docs, new_metas, new_distances = retrieve(video_id, rewritten_vec)
                new_confidence, new_label = score_confidence(new_distances)

                # Use rewritten results only if they're better
                if new_distances and (not distances or min(new_distances) < min(distances)):
                    docs, metas, distances = new_docs, new_metas, new_distances
                    confidence_level, confidence_label = new_confidence, new_label
                    is_corrected = True
        except Exception:
            pass  # If rewrite fails, continue with original results

    # ── Nothing found even after correction ───────────────────────────────
    if not docs or confidence_level == "low":
        not_found_msg = "This topic is not covered in the video."
        if lang_code != "en":
            try:
                model = get_gemini_client()
                lang_name = LANGUAGE_NAMES.get(lang_code, lang_code)
                resp = model.generate_content(
                    f'Translate only to {lang_name}: "{not_found_msg}"'
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
        prompt = build_qa_prompt(question, docs, metas, history, lang_code, is_corrected)
        resp   = model.generate_content(prompt)
        answer = resp.text.strip()
    except Exception as e:
        msg = str(e)
        if "429" in msg or "quota" in msg.lower():
            error("RATE_LIMITED", "Gemini rate limit. Please try again in 30 seconds.")
        else:
            error("API_ERROR", f"Gemini error: {msg}")

    # ── Append confidence to answer ───────────────────────────────────────
    final_answer = f"{answer}\n\n{confidence_label}"
    if is_corrected:
        final_answer += " _(query auto-corrected)_"

    # ── Save to history ───────────────────────────────────────────────────
    _save_turn(user_id, "user", question)
    _save_turn(user_id, "bot",  answer)

    ok({"answer": final_answer, "confidence": confidence_level})


if __name__ == "__main__":
    main()
