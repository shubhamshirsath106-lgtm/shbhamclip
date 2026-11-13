---
title: AutoClip
emoji: 🎬
sdk: docker
app_port: 7860
---

# AutoClip (English)

Paste a video link, get AI-picked highlight clips. Mobile-friendly website + Python backend.

## Run on your computer (most reliable for YouTube)
1. Install Python 3.10+, [ffmpeg](https://ffmpeg.org/download.html) and [Deno](https://deno.com) (needed by yt-dlp for YouTube).
2. `pip install -U -r requirements.txt`
3. Start a model: install [Ollama](https://ollama.com), then `ollama pull qwen2.5:7b`
4. `python app.py` and open http://localhost:7860 (phone on same Wi-Fi: http://YOUR-PC-IP:7860)
5. Paste a YouTube link, tap **Create clips**.

## Deploy free on Hugging Face Spaces
Create a Space with SDK **Docker**, upload all files. Use a cloud model (enter base URL, model and API key in "AI settings"), since Ollama can't run there. Note: YouTube often blocks cloud IPs; if downloads fail, set a `YTDLP_COOKIES` secret or use the upload option.

## Website on GitHub Pages + separate backend
Host the `website/` folder on GitHub Pages, run `app.py` elsewhere (https), set `window.AUTOCLIP_API` in `website/config.js`, and set `ALLOWED_ORIGIN` on the backend.

## Settings (environment variables)
`MAX_MINUTES` (default 30), `MAX_UPLOAD_MB` (500), `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `YTDLP_COOKIES`, `YTDLP_ARGS`, `ALLOWED_ORIGIN`, `PORT`.

## Troubleshooting
- "Could not download": update yt-dlp (`pip install -U "yt-dlp[default]"`) and make sure Deno is installed.
- "No highlights found": lower **Min score** to 0.5, or use a stronger model.
- No subtitles on the video: install `faster-whisper`, or upload an `.srt`.

Use only videos you own or have permission to use. MIT license.
