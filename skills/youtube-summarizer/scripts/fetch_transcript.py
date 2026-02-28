#!/usr/bin/env python3
import sys, json, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from utils import extract_video_id, get_cache, ok, error

CACHE_TTL = 60 * 60 * 24 * 7

def fetch_from_youtube(video_id):
    from youtube_transcript_api import YouTubeTranscriptApi
    ytt = YouTubeTranscriptApi()
    try:
        try:
            t = ytt.fetch(video_id, languages=["en"])
            return normalise(t)
        except Exception:
            pass
        tlist = ytt.list(video_id)
        for t in tlist:
            try:
                return normalise(t.fetch())
            except Exception:
                continue
        error("NO_TRANSCRIPT", "No captions found for this video.")
    except Exception as e:
        msg = str(e)
        if "429" in msg: error("RATE_LIMITED", "YouTube rate limit. Try again in a minute.")
        elif "disabled" in msg.lower(): error("NO_TRANSCRIPT", "Captions disabled on this video.")
        elif "unavailable" in msg.lower(): error("VIDEO_NOT_FOUND", "Video not found or private.")
        else: error("TRANSCRIPT_ERROR", f"Unexpected error: {msg}")

def normalise(transcript):
    result = []
    for seg in transcript:
        if hasattr(seg, "text"):
            result.append({"text": seg.text, "start": float(seg.start), "duration": float(seg.duration)})
        elif isinstance(seg, dict):
            result.append({"text": seg.get("text",""), "start": float(seg.get("start",0)), "duration": float(seg.get("duration",2.0))})
    return result

def clean(segments):
    noise = re.compile(r"\[.*?\]|\(.*?\)")
    cleaned = []
    for seg in segments:
        text = noise.sub("", seg["text"]).strip()
        text = re.sub(r"\s+", " ", text)
        if len(text) > 2:
            cleaned.append({"text": text, "start": seg["start"], "duration": seg["duration"]})
    return cleaned

def main():
    if len(sys.argv) < 3:
        error("USAGE", "python3 fetch_transcript.py <url> <user_id>")
    raw_input = sys.argv[1].strip()
    user_id   = sys.argv[2].strip()
    video_id  = extract_video_id(raw_input)
    if not video_id:
        if re.fullmatch(r"[a-zA-Z0-9_-]{11}", raw_input):
            video_id = raw_input
        else:
            error("INVALID_URL", "Not a valid YouTube link. Try: youtube.com/watch?v=XXXXXXXXXXX")
    cache = get_cache()
    key   = f"transcript:{video_id}"
    if key in cache:
        cached = cache[key]
        print(json.dumps({"status":"cache_hit","video_id":video_id,"title":cached.get("title","YouTube Video"),"duration":cached.get("duration",0)}))
        sys.exit(0)
    segments = fetch_from_youtube(video_id)
    segments = clean(segments)
    if not segments:
        error("NO_TRANSCRIPT", "Transcript empty after cleaning.")
    last     = segments[-1]
    duration = last["start"] + last.get("duration", 0)
    cache.set(key, {"video_id":video_id,"title":"YouTube Video","segments":segments,"duration":duration}, expire=CACHE_TTL)
    ok({"video_id":video_id,"title":"YouTube Video","duration":round(duration,1),"transcript_length":len(segments)})

if __name__ == "__main__":
    main()
