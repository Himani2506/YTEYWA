#!/usr/bin/env python3
"""
process_video.py v2.0 — Enhanced RAG Pipeline

Upgrades:
  1. Topic-Aware Temporal Segmentation
     - Detects topic boundaries by measuring cosine similarity between adjacent windows
     - Sharp similarity drop = topic change → creates semantically complete chunks
     
  2. Parent-Child Retrieval (Small-to-Big)
     - Child chunks (~300 chars) → stored in ChromaDB for precise retrieval
     - Parent chunks (~1500 chars) → stored in metadata for rich context delivery
     - Retrieval uses child precision, generation uses parent context

Usage:
  python3 process_video.py "<video_id>" "<user_id>"

Output:
  {"status":"ok", "video_id":"...", "chunks_created":42, "topics_detected":8}
  {"status":"ok", "video_id":"...", "chunks_created":0, "note":"already processed"}
"""

import sys
import json
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from utils import get_cache, get_chroma_collection, ok, error, seconds_to_timestamp

MAX_CHUNKS  = 200
BATCH_SIZE  = 32
WINDOW_SECS = 15.0        # Shorter windows for finer topic detection

# Topic segmentation thresholds
SIMILARITY_DROP_THRESHOLD = 0.25   # Drop below this = topic boundary
MIN_TOPIC_WINDOWS         = 3      # Minimum windows per topic segment

# Parent-child sizes
CHILD_CHUNK_CHARS  = 400           # Small — precise retrieval
PARENT_CHUNK_CHARS = 1800          # Large — rich context for Gemini


# ── Step 1: Merge segments into short windows ──────────────────────────────
def merge_windows(segments: list, window_secs: float = WINDOW_SECS) -> list:
    if not segments:
        return []
    windows, buf, buf_start = [], [], segments[0]["start"]
    for seg in segments:
        if seg["start"] - buf_start <= window_secs:
            buf.append(seg["text"])
        else:
            if buf:
                windows.append({"text": " ".join(buf), "start": buf_start})
            buf, buf_start = [seg["text"]], seg["start"]
    if buf:
        windows.append({"text": " ".join(buf), "start": buf_start})
    return windows


# ── Step 2: Embed all windows for topic detection ─────────────────────────
def embed_texts(texts: list, model) -> np.ndarray:
    all_vecs = []
    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        vecs  = model.encode(batch, show_progress_bar=False, convert_to_numpy=True)
        all_vecs.append(vecs)
    return np.vstack(all_vecs)


# ── Step 3: Topic-Aware Segmentation ──────────────────────────────────────
def detect_topic_boundaries(window_embeddings: np.ndarray) -> list[int]:
    """
    Find indices where topic changes by measuring cosine similarity
    between adjacent window embeddings.
    
    Sharp drop in similarity = topic boundary.
    Returns list of boundary indices.
    """
    if len(window_embeddings) < 2:
        return []

    boundaries = []
    similarities = []

    for i in range(len(window_embeddings) - 1):
        a = window_embeddings[i]
        b = window_embeddings[i + 1]
        # Cosine similarity
        sim = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))
        similarities.append(sim)

    if not similarities:
        return []

    # Adaptive threshold: use mean - 0.5*std as boundary detector
    mean_sim = np.mean(similarities)
    std_sim  = np.std(similarities)
    threshold = max(SIMILARITY_DROP_THRESHOLD, mean_sim - 0.5 * std_sim)

    for i, sim in enumerate(similarities):
        if sim < threshold:
            boundaries.append(i + 1)  # i+1 = start of new topic

    return boundaries


def segment_into_topics(windows: list, boundaries: list[int]) -> list[list[dict]]:
    """
    Split windows into topic segments using detected boundaries.
    Ensures minimum segment size.
    """
    if not boundaries:
        return [windows]

    segments = []
    prev = 0
    for boundary in boundaries:
        segment = windows[prev:boundary]
        if len(segment) >= MIN_TOPIC_WINDOWS:
            segments.append(segment)
        elif segments:
            # Merge tiny segment into previous
            segments[-1].extend(segment)
        else:
            segments.append(segment)
        prev = boundary

    # Last segment
    last = windows[prev:]
    if last:
        if len(last) < MIN_TOPIC_WINDOWS and segments:
            segments[-1].extend(last)
        else:
            segments.append(last)

    return segments


# ── Step 4: Parent-Child chunk creation ───────────────────────────────────
def create_parent_child_chunks(topic_segments: list[list[dict]]) -> list[dict]:
    """
    For each topic segment:
    - Create one PARENT chunk (full topic text, ~1500 chars)
    - Create multiple CHILD chunks (~400 chars each) that point to the parent
    
    Returns list of child chunks with parent_text in metadata.
    """
    all_children = []

    for seg_idx, segment in enumerate(topic_segments):
        # Build parent text from all windows in this topic
        parent_text  = " ".join(w["text"] for w in segment)
        parent_start = segment[0]["start"]
        parent_ts    = seconds_to_timestamp(parent_start)

        # Trim parent to max size
        if len(parent_text) > PARENT_CHUNK_CHARS:
            parent_text = parent_text[:PARENT_CHUNK_CHARS]

        # Create child chunks from the same topic segment
        # Split parent into smaller child pieces
        words   = parent_text.split()
        child_word_size = max(1, CHILD_CHUNK_CHARS // 5)  # ~80 words per child
        overlap = child_word_size // 4

        child_idx = 0
        pos = 0
        while pos < len(words):
            child_words = words[pos:pos + child_word_size]
            child_text  = " ".join(child_words)

            if len(child_text) > 10:
                # Find the window closest to this position in the segment
                window_pos = min(
                    int(pos / max(len(words), 1) * len(segment)),
                    len(segment) - 1
                )
                child_start = segment[window_pos]["start"]

                all_children.append({
                    "child_text":  child_text,
                    "parent_text": parent_text,
                    "start":       child_start,
                    "timestamp":   seconds_to_timestamp(child_start),
                    "parent_ts":   parent_ts,
                    "topic_idx":   seg_idx,
                    "child_idx":   child_idx,
                })
                child_idx += 1

            pos += child_word_size - overlap

    return all_children


# ── Step 5: Store in ChromaDB ──────────────────────────────────────────────
def store_chunks(video_id: str, chunks: list[dict], model) -> int:
    """
    Embed CHILD texts and store in ChromaDB.
    Parent text is stored in metadata for retrieval.
    """
    collection = get_chroma_collection(video_id)

    # Process in batches
    batch_size = 32
    stored = 0

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]

        child_texts = [c["child_text"] for c in batch]
        embeddings  = model.encode(
            child_texts,
            show_progress_bar=False,
            convert_to_list=True,
        )

        ids = [f"{video_id}-t{c['topic_idx']}-c{c['child_idx']}" for c in batch]
        metadatas = [
            {
                "start":       c["start"],
                "timestamp":   c["timestamp"],
                "parent_text": c["parent_text"],
                "parent_ts":   c["parent_ts"],
                "topic_idx":   c["topic_idx"],
                "video_id":    video_id,
            }
            for c in batch
        ]

        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=child_texts,
            metadatas=metadatas,
        )
        stored += len(batch)

    return stored


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
        error("CACHE_MISS", f"Transcript not found. Run fetch_transcript.py first.")

    segments = cache[key]["segments"]

    # Check if already processed
    collection = get_chroma_collection(video_id)
    if collection.count() > 0:
        print(json.dumps({
            "status": "ok", "video_id": video_id,
            "chunks_created": 0, "note": "already processed",
        }))
        sys.exit(0)

    # Load embedding model
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
    except ImportError:
        error("MISSING_DEP", "sentence-transformers not installed.")

    # Step 1: Merge into windows
    windows = merge_windows(segments)
    if not windows:
        error("EMPTY_TRANSCRIPT", "No usable content after merging.")

    # Step 2: Embed all windows
    window_texts      = [w["text"] for w in windows]
    window_embeddings = embed_texts(window_texts, model)

    # Step 3: Detect topic boundaries
    boundaries     = detect_topic_boundaries(window_embeddings)
    topic_segments = segment_into_topics(windows, boundaries)
    topics_detected = len(topic_segments)

    # Step 4: Create parent-child chunks
    chunks = create_parent_child_chunks(topic_segments)
    if not chunks:
        error("CHUNKING_FAILED", "Could not create chunks.")

    # Cap at MAX_CHUNKS
    if len(chunks) > MAX_CHUNKS:
        chunks = chunks[:MAX_CHUNKS]

    # Step 5: Store in ChromaDB
    stored = store_chunks(video_id, chunks, model)

    # Cache chunks for summary generation
    # For summary, use parent chunks (richer content)
    parent_chunks = []
    seen_parents  = set()
    for c in chunks:
        key_p = c["parent_text"][:50]
        if key_p not in seen_parents:
            seen_parents.add(key_p)
            parent_chunks.append({
                "text":      c["parent_text"],
                "start":     c["start"],
                "timestamp": c["parent_ts"],
            })

    cache.set(f"chunks:{video_id}", {"chunks": parent_chunks}, expire=60*60*24*7)

    ok({
        "video_id":       video_id,
        "chunks_created": stored,
        "topics_detected": topics_detected,
    })


if __name__ == "__main__":
    main()
