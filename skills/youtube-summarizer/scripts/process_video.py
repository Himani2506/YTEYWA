#!/usr/bin/env python3
"""
process_video.py — Chunk transcript, embed with MiniLM, store in ChromaDB.

Usage:
  python3 process_video.py "<video_id>" "<user_id>"

Output:
  {"status":"ok", "video_id":"...", "chunks_created":42}
  {"status":"ok", "video_id":"...", "chunks_created":0, "note":"already processed"}
  {"status":"error", ...}
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import get_cache, get_chroma_collection, ok, error, seconds_to_timestamp

MAX_CHUNKS  = 200    # Cap for very long videos
BATCH_SIZE  = 32     # Embedding batch size (memory-friendly on CPU)
WINDOW_SECS = 30.0   # Merge transcript segments into 30-second windows
CHUNK_CHARS = 3000   # ~750 tokens per chunk
OVERLAP_CHARS = 300  # ~75 token overlap between adjacent chunks


# ── Step 1: Merge tiny segments into windows ───────────────────────────────
def merge_windows(segments: list) -> list:
    if not segments:
        return []
    windows, buf_text, buf_start = [], [], segments[0]["start"]
    for seg in segments:
        if seg["start"] - buf_start <= WINDOW_SECS:
            buf_text.append(seg["text"])
        else:
            if buf_text:
                windows.append({"text": " ".join(buf_text), "start": buf_start})
            buf_text, buf_start = [seg["text"]], seg["start"]
    if buf_text:
        windows.append({"text": " ".join(buf_text), "start": buf_start})
    return windows


# ── Step 2: Split windows into overlapping chunks ─────────────────────────
def split_chunks(windows: list) -> list:
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError:
        error("MISSING_DEP", "langchain-text-splitters not installed. Run: pip install langchain-text-splitters")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_CHARS,
        chunk_overlap=OVERLAP_CHARS,
        separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""],
    )

    chunks = []
    buf_text, buf_start = "", None

    def flush(text, start):
        if not text.strip():
            return
        for piece in splitter.split_text(text):
            chunks.append({
                "text":      piece,
                "start":     start,
                "timestamp": seconds_to_timestamp(start),
            })

    for w in windows:
        candidate = (buf_text + " " + w["text"]).strip() if buf_text else w["text"]
        if len(candidate) > CHUNK_CHARS * 1.5:
            flush(buf_text, buf_start)
            buf_text, buf_start = w["text"], w["start"]
        else:
            buf_text = candidate
            if buf_start is None:
                buf_start = w["start"]

    flush(buf_text, buf_start)

    # Cap at MAX_CHUNKS: drop shortest chunks first
    if len(chunks) > MAX_CHUNKS:
        chunks.sort(key=lambda c: len(c["text"]), reverse=True)
        chunks = chunks[:MAX_CHUNKS]
        chunks.sort(key=lambda c: c["start"])

    return chunks


# ── Step 3: Embed ──────────────────────────────────────────────────────────
def embed(texts: list) -> list:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        error("MISSING_DEP", "sentence-transformers not installed. Run: pip install sentence-transformers")

    model = SentenceTransformer("all-MiniLM-L6-v2")
    embeddings = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        vecs  = model.encode(batch, show_progress_bar=False, convert_to_list=True)
        embeddings.extend(vecs)
    return embeddings


# ── Main ───────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 3:
        error("USAGE", "python3 process_video.py <video_id> <user_id>")

    video_id = sys.argv[1].strip()
    user_id  = sys.argv[2].strip()

    # Load transcript from cache
    cache = get_cache()
    key   = f"transcript:{video_id}"
    if key not in cache:
        error("CACHE_MISS", f"Transcript not found for {video_id}. Run fetch_transcript.py first.")

    segments = cache[key]["segments"]

    # Check if already embedded
    collection = get_chroma_collection(video_id)
    if collection.count() > 0:
        print(json.dumps({
            "status": "ok", "video_id": video_id,
            "chunks_created": 0, "note": "already processed",
        }))
        sys.exit(0)

    # Process
    windows = merge_windows(segments)
    if not windows:
        error("EMPTY_TRANSCRIPT", "Transcript had no usable content.")

    chunks = split_chunks(windows)
    if not chunks:
        error("CHUNKING_FAILED", "Could not create chunks from transcript.")

    # Embed and store
    texts      = [c["text"] for c in chunks]
    embeddings = embed(texts)

    collection.add(
        ids        = [f"{video_id}-{i}" for i in range(len(chunks))],
        embeddings = embeddings,
        documents  = texts,
        metadatas  = [{"start": c["start"], "timestamp": c["timestamp"], "video_id": video_id} for c in chunks],
    )

    # Cache chunks for fast summary access
    cache.set(f"chunks:{video_id}", {"chunks": chunks}, expire=60*60*24*7)

    ok({"video_id": video_id, "chunks_created": len(chunks)})


if __name__ == "__main__":
    main()
