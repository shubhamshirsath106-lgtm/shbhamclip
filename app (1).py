#!/usr/bin/env python3
"""
app.py - mobile-friendly web UI for autoclip_en.py

Run locally:   python app.py   ->  open http://localhost:7860 (on your phone use your PC's IP)
Production:    gunicorn -w 1 --threads 8 --timeout 600 -b 0.0.0.0:7860 app:app
NOTE: keep a single worker (-w 1); job state is kept in memory.
"""
import glob
import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from argparse import Namespace

from flask import Flask, jsonify, request, send_from_directory

import autoclip_en as core

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs")
os.makedirs(BASE, exist_ok=True)
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
JOB_TTL = 2 * 3600  # delete jobs after 2 hours
MAX_MIN = int(os.getenv("MAX_MINUTES", "30"))  # longest video accepted

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024
JOBS = {}
LOCK = threading.Lock()


def cleanup():
    now = time.time()
    for name in os.listdir(BASE):
        path = os.path.join(BASE, name)
        if os.path.isdir(path) and now - os.path.getmtime(path) > JOB_TTL:
            shutil.rmtree(path, ignore_errors=True)
            JOBS.pop(name, None)


def set_job(job_id, **kw):
    with LOCK:
        JOBS[job_id].update(kw)


def download(url, folder, job_id):
    """Download video (<=720p) and any English subtitles with yt-dlp."""
    set_job(job_id, stage="Downloading video", pct=3)
    cmd = ["yt-dlp", "--no-playlist", "--no-warnings", "--retries", "3", "--socket-timeout", "30",
           "-f", "bv*[height<=720]+ba/b[height<=720]/b",
           "--merge-output-format", "mp4", "--write-subs", "--write-auto-subs",
           "--sub-langs", "en.*,en", "--convert-subs", "srt",
           "--match-filter", "duration<=%d" % (MAX_MIN * 60),
           "-o", os.path.join(folder, "input.%(ext)s")]
    cookies = os.getenv("YTDLP_COOKIES", "")  # optional cookies.txt path (helps with "confirm you're not a bot")
    if cookies and os.path.isfile(cookies):
        cmd += ["--cookies", cookies]
    cmd += shlex.split(os.getenv("YTDLP_ARGS", ""))  # optional extra yt-dlp flags
    cmd += ["--", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        raise RuntimeError("yt-dlp is not installed on the server (pip install -U 'yt-dlp[default]').")
    except subprocess.TimeoutExpired:
        raise RuntimeError("Download took too long.")
    video = next((p for p in glob.glob(os.path.join(folder, "input.*"))
                  if os.path.splitext(p)[1].lower() in VIDEO_EXT), None)
    # A failed subtitle fetch (e.g. HTTP 429) makes yt-dlp exit non-zero even when the video is fine.
    if not video:
        err = (r.stderr or "").strip()
        low = err.lower()
        if "not a bot" in low or "sign in" in low:
            hint = "YouTube is asking for sign-in/bot check. Set YTDLP_COOKIES to a cookies.txt file, or run the site on your own computer."
        elif "javascript runtime" in low or "challenge" in low:
            hint = "Install Deno on the server and update yt-dlp (pip install -U 'yt-dlp[default]')."
        elif r.returncode == 0:
            hint = "The video is private, live, or longer than %d minutes." % MAX_MIN
        else:
            hint = (err.splitlines() or ["unknown error"])[-1][:200]
        raise RuntimeError("Could not download this link. " + hint)
    srts = glob.glob(os.path.join(folder, "*.srt"))
    return video, (srts[0] if srts else None)


def clean_cues(cues):
    """YouTube auto-captions repeat text in rolling cues; drop the repeats."""
    out = []
    for c in cues:
        t = c["text"]
        if out:
            prev = out[-1]["text"]
            if t == prev or prev.endswith(t):
                continue
            if t.startswith(prev):
                t = t[len(prev):].strip()
                if not t:
                    continue
        out.append({"start": c["start"], "end": c["end"], "text": t})
    return out


def worker(job_id, video, srt, args, url=None):
    out = os.path.join(BASE, job_id, "output")
    try:
        if url:
            video, srt = download(url, os.path.join(BASE, job_id), job_id)
        if core.video_duration(video) > MAX_MIN * 60:
            raise RuntimeError("Video is longer than %d minutes." % MAX_MIN)
        set_job(job_id, stage="Reading subtitles" if srt else "Transcribing audio", pct=8)
        cues = clean_cues(core.parse_srt(srt)) if srt else core.transcribe(video)
        if not cues:
            raise RuntimeError("No transcript content found.")

        chunks = core.chunk_cues(cues, args.chunk_chars)

        def on_chunk(n, total):
            set_job(job_id, stage="Analyzing %d/%d" % (n, total), pct=10 + int(60 * (n - 1) / total))

        hl = core.find_highlights(chunks, args, core.video_duration(video), on_chunk)
        if not hl:
            raise RuntimeError("No highlights found. Try a lower score threshold (e.g. 0.5).")

        os.makedirs(out, exist_ok=True)
        paths, clips = [], []
        for i, h in enumerate(hl, 1):
            set_job(job_id, stage="Cutting clip %d/%d" % (i, len(hl)), pct=70 + int(25 * (i - 1) / len(hl)))
            name = "%02d_%s.mp4" % (i, core.safe_name(h["title"]))
            core.cut_clip(video, h, os.path.join(out, name))
            paths.append(os.path.join(out, name))
            clips.append({"file": name, "title": h["title"], "score": round(h["score"], 2),
                          "start": core.sec_to_ts(h["start"]), "end": core.sec_to_ts(h["end"])})

        collection = None
        top = sorted(range(len(hl)), key=lambda k: -hl[k]["score"])[:args.max_collection]
        if len(top) > 1:
            set_job(job_id, stage="Building collection", pct=96)
            core.make_collection([paths[k] for k in sorted(top)], os.path.join(out, "collection.mp4"))
            collection = "collection.mp4"

        os.remove(video)  # free disk space
        set_job(job_id, status="done", stage="Done", pct=100, clips=clips, collection=collection)
    except (Exception, SystemExit) as e:
        set_job(job_id, status="error", stage="Failed", error=str(e) or e.__class__.__name__)


@app.post("/start")
def start():
    cleanup()
    url = (request.form.get("url") or "").strip()
    f = request.files.get("video")
    has_file = bool(f and f.filename)
    if url and not url.lower().startswith(("http://", "https://")):
        return jsonify(error="Please paste a valid link starting with http."), 400
    if not url and not has_file:
        return jsonify(error="Paste a video link or choose a file."), 400

    job_id = uuid.uuid4().hex
    folder = os.path.join(BASE, job_id)
    video = srt = None
    if not url:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in VIDEO_EXT:
            return jsonify(error="Unsupported video type."), 400
        os.makedirs(folder)
        video = os.path.join(folder, "input" + ext)
        f.save(video)
        sf = request.files.get("srt")
        if sf and sf.filename:
            srt = os.path.join(folder, "input.srt")
            sf.save(srt)
    else:
        os.makedirs(folder)

    form = request.form

    def num(key, default, cast):
        try:
            return cast(form.get(key) or default)
        except ValueError:
            return cast(default)

    args = Namespace(
        base_url=form.get("base_url") or os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
        api_key=form.get("api_key") or os.getenv("LLM_API_KEY", ""),  # never written to disk
        model=form.get("model") or os.getenv("LLM_MODEL", "qwen2.5:7b"),
        chunk_chars=5000,
        min_score=min(max(num("min_score", 0.6, float), 0.0), 1.0),
        min_len=15,
        max_len=min(max(num("max_len", 90, int), 15), 300),
        max_collection=5, timeout=60, retries=3,
    )
    with LOCK:
        JOBS[job_id] = {"status": "running", "stage": "Queued", "pct": 1}
    threading.Thread(target=worker, args=(job_id, video, srt, args, url or None), daemon=True).start()
    return jsonify(id=job_id)


@app.get("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(status="error", error="Job not found or expired."), 404
    return jsonify(job)


@app.get("/files/<job_id>/<path:name>")
def files(job_id, name):
    if job_id not in JOBS:
        return "Not found", 404
    return send_from_directory(os.path.join(BASE, job_id, "output"), name,
                               as_attachment=request.args.get("dl") == "1")


SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "website")


@app.get("/")
def index():
    return send_from_directory(SITE, "index.html")


@app.get("/<any(style.css,script.js,config.js):name>")
def site_asset(name):
    return send_from_directory(SITE, name)


@app.get("/config")
def config():
    return jsonify(max_minutes=MAX_MIN)


@app.after_request
def cors(resp):
    # Lets the website be hosted elsewhere (e.g. GitHub Pages). Set ALLOWED_ORIGIN to your site URL.
    resp.headers["Access-Control-Allow-Origin"] = os.getenv("ALLOWED_ORIGIN", "*")
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "7860")))
