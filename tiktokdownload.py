from flask import Flask, request, jsonify, send_from_directory
from threading import Thread, Lock
import requests
import yt_dlp
from yt_dlp.postprocessor import FFmpegPostProcessor
import os
import subprocess
import sys
import time
import traceback
import shutil
from pathlib import Path
from urllib.parse import urlparse
import uuid
import webbrowser

# Server configuration
app = Flask(__name__, static_folder="static", static_url_path="/static")
data = {"url": "", "transcription": ""}
lock = Lock()
script_dir = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = Path(__file__).resolve().parent
VIDEO_DIR = (BASE_DIR / "static" / "videos").resolve()
AUDIO_DIR = (BASE_DIR / "static" / "audio").resolve()
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Path to saved mp4 for current job
video_path = None

# Set at startup by prompt_transcribe_choice()
TRANSCRIBE_ENABLED = True


def prompt_transcribe_choice():
    """Console menu: arrow keys + Enter to choose whether to transcribe videos."""
    options = ["Yes - transcribe videos (videos in videos/, audio in audio/)", "No - download only (video in videos/)"]
    selected = 0

    def clear_and_render():
        if sys.platform == "win32":
            os.system("cls")
        else:
            os.system("clear")
        print("\n  Transcribe videos?\n")
        for i, opt in enumerate(options):
            prefix = "  > " if i == selected else "    "
            print(f"{prefix} {opt}")
        print("\n  Use ↑/↓ to move, Enter to select\n")

    if sys.platform == "win32":
        try:
            import msvcrt
            while True:
                clear_and_render()
                ch = msvcrt.getch()
                if ch == b"\xe0":
                    ch2 = msvcrt.getch()
                    if ch2 == b"H":  # Up
                        selected = (selected - 1) % len(options)
                    elif ch2 == b"P":  # Down
                        selected = (selected + 1) % len(options)
                elif ch in (b"\r", b"\n"):  # Enter
                    return selected == 0
        except ImportError:
            pass
    # Fallback: simple prompt
    print("\n  Transcribe videos?")
    for i, opt in enumerate(options):
        print(f"  {i + 1}. {opt}")
    while True:
        try:
            choice = input("  Enter 1 or 2: ").strip()
            if choice in ("1", "2"):
                print()
                return choice == "1"
        except (EOFError, KeyboardInterrupt):
            print("\n")
            return True


def normalize_url_for_dedup(url):
    """Return a canonical key for the same video (ignores tracking params, trailing slash)."""
    if not url or not url.strip():
        return ""
    parsed = urlparse(url.strip())
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    # TikTok: tiktok.com/t/XXX and vm.tiktok.com/XXX resolve to same video
    if "tiktok" in netloc and path and path != "/":
        code = path.strip("/").split("/")[-1]  # last segment (handles /t/XXX and /XXX)
        if code:
            return f"tiktok:{code}"
    # Instagram: /reel/XXX
    if "instagram" in netloc and "/reel/" in path:
        parts = path.split("/reel/")
        if len(parts) >= 2 and parts[-1]:
            return f"instagram:reel:{parts[-1].split('/')[0]}"
    # Fallback: netloc + path
    return f"{netloc}{path}"


def find_ffmpeg_bin():
    """Return path to directory containing ffmpeg.exe and ffprobe.exe, or None."""
    local = os.environ.get("LOCALAPPDATA", "")
    # Prefer real bin over PATH (winget Links can be symlinks; some tools prefer real path)
    winget_gyan = Path(local) / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    if winget_gyan.exists():
        for sub in winget_gyan.iterdir():
            if sub.is_dir():
                bin_dir = sub / "bin"
                if (bin_dir / "ffmpeg.exe").exists() and (bin_dir / "ffprobe.exe").exists():
                    return str(bin_dir)
    ff = shutil.which("ffmpeg")
    fp = shutil.which("ffprobe")
    if ff and fp:
        return str(Path(ff).parent)
    winget_base = Path(local) / "Microsoft" / "WinGet" / "Packages"
    if winget_base.exists():
        for sub in winget_base.rglob("ffmpeg.exe"):
            bin_dir = sub.parent
            if (bin_dir / "ffprobe.exe").exists():
                return str(bin_dir)
    return None


def is_valid_video_url(url):
    """Reject placeholders, self-URLs, and obviously invalid URLs."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip().lower()
    if not u or u == "..." or u.startswith("ytsearch:"):
        return False
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    # Reject URLs pointing to this app (shortcut may send server URL by mistake)
    parsed = urlparse(u)
    host = (parsed.netloc or "").split(":")[0]
    if host in ("localhost", "127.0.0.1", "pkm.local", "0.0.0.0") or host.endswith(".local"):
        return False
    # Must look like a video platform
    if any(x in host for x in ("tiktok", "instagram", "youtube", "youtu.be", "vm.tiktok", "x.com", "twitter")):
        return True
    return True  # Allow other http(s) URLs (e.g. other platforms)


def is_x_url(url):
    """Check if URL is from X (Twitter)."""
    if not url:
        return False
    parsed = urlparse(url.strip().lower())
    host = (parsed.netloc or "").split(":")[0]
    return "x.com" in host or "twitter.com" in host


def convert_mp4_to_gif(ffmpeg_bin, input_path, output_path):
    """Convert video (mp4/webm) to gif using ffmpeg with palette for quality."""
    bin_dir = Path(ffmpeg_bin)
    ffmpeg_exe = str(bin_dir / "ffmpeg.exe") if os.name == "nt" else str(bin_dir / "ffmpeg")
    if not os.path.exists(ffmpeg_exe):
        ffmpeg_exe = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [
        ffmpeg_exe, "-y", "-i", input_path,
        "-filter_complex", "[0:v] split [a][b];[a] palettegen [p];[b][p] paletteuse",
        "-f", "gif", output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and os.path.exists(output_path)


def is_likely_x_gif(ydl, url):
    """Try to detect if X URL is a GIF (short looping video). Returns True if likely GIF."""
    try:
        info = ydl.extract_info(url, download=False)
        if not info:
            return False
        # X GIFs: usually short (< 30 sec)
        duration = info.get("duration")
        if duration is not None:
            return duration <= 30  # GIFs are typically 2–15 sec
        # No duration: could be GIF, be conservative and try conversion for short files
        return True
    except Exception:
        return False


@app.route('/set_url', methods=['POST'])
def set_url():
    content = request.json or {}
    new_url = content.get('url', '').strip()
    if not is_valid_video_url(new_url):
        print(f"[!] Rejected invalid URL: {repr(new_url)[:50]}")
        return jsonify({"status": "Invalid URL", "error": "Provide a valid TikTok, Instagram, or YouTube URL"}), 400
    new_key = normalize_url_for_dedup(new_url)
    with lock:
        current_key = normalize_url_for_dedup(data.get("url", ""))
        if new_key and new_key == current_key:
            return jsonify({"status": "URL already set"})
        data["url"] = new_url
        data["transcription"] = ""
        global video_path
        video_path = None
        print(f"[+] URL received: {data['url']}")
    return jsonify({"status": "URL received"})

@app.route('/get_transcription', methods=['GET'])
def get_transcription():
    global video_path
    url = ""
    with lock:
        # Build video URL if file exists
        if video_path and os.path.exists(video_path):
            base = request.host_url[:-1] if request.host_url.endswith("/") else request.host_url
            rel = video_path.replace(str(BASE_DIR) + os.sep, "").replace("\\", "/")
            url = f"{base}/{rel}"

        return jsonify({
            "transcription": data.get("transcription", ""),
            "video_url": url
        })

def worker():
    last_processed_key = ""
    model = None  # Lazy-loaded when first URL needs transcription
    ffmpeg_bin = None  # Lazy-loaded on first URL (speeds up startup)

    while True:
        with lock:
            url = data["url"]

        url_key = normalize_url_for_dedup(url)
        if url and is_valid_video_url(url) and url_key and url_key != last_processed_key:
            # Lazy-init ffmpeg on first URL (speeds up startup)
            if ffmpeg_bin is None:
                ffmpeg_bin = find_ffmpeg_bin()
                if ffmpeg_bin:
                    FFmpegPostProcessor._ffmpeg_location.set(ffmpeg_bin)
                    print(f"[+] Using ffmpeg from: {ffmpeg_bin}")
                else:
                    print("[!] ffmpeg not found. Install it for full support (Instagram, postprocessing).")
                print(f"[+] Videos save to: {VIDEO_DIR}")
                if TRANSCRIBE_ENABLED:
                    print(f"[+] Audio saves to: {AUDIO_DIR}")

            print(f"[+] Processing new URL: {url}")

            try:
                # Download new video into static/videos with a unique id
                job_id = uuid.uuid4().hex
                outtmpl = str(VIDEO_DIR / f"{job_id}.%(ext)s")

                has_ffmpeg = ffmpeg_bin is not None

                if has_ffmpeg and TRANSCRIBE_ENABLED:
                    ydl_opts = {
                        "paths": {"home": str(VIDEO_DIR), "temp": str(VIDEO_DIR)},
                        "outtmpl": outtmpl,
                        "format": "bv*+ba/best",
                        "hls_prefer_native": False,
                        "skip_unavailable_fragments": True,
                        "fragment_retries": 20,
                        "retries": 10,
                        "concurrent_fragment_downloads": 8,
                        "noplaylist": True,
                        "keepvideo": True,
                        "postprocessors": [
                            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
                            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "0"},
                        ],
                        "merge_output_format": "mp4",
                        "http_headers": {"User-Agent": "Mozilla/5.0"},
                        "extractor_args": {"youtube": {"player_client": ["web", "ios", "android"]}},
                    }
                elif has_ffmpeg and not TRANSCRIBE_ENABLED:
                    ydl_opts = {
                        "paths": {"home": str(VIDEO_DIR), "temp": str(VIDEO_DIR)},
                        "outtmpl": outtmpl,
                        "format": "bv*+ba/best",
                        "hls_prefer_native": False,
                        "skip_unavailable_fragments": True,
                        "fragment_retries": 20,
                        "retries": 10,
                        "concurrent_fragment_downloads": 8,
                        "noplaylist": True,
                        "postprocessors": [
                            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
                        ],
                        "merge_output_format": "mp4",
                        "http_headers": {"User-Agent": "Mozilla/5.0"},
                        "extractor_args": {"youtube": {"player_client": ["web", "ios", "android"]}},
                    }
                else:
                    # No ffmpeg: use single-format only (no merge), no postprocessors
                    # Instagram DASH may still fail - install ffmpeg for full support
                    ydl_opts = {
                        "paths": {"home": str(VIDEO_DIR), "temp": str(VIDEO_DIR)},
                        "outtmpl": outtmpl,
                        "format": "best[ext=mp4]/best",
                        "hls_prefer_native": False,
                        "skip_unavailable_fragments": True,
                        "fragment_retries": 20,
                        "retries": 10,
                        "concurrent_fragment_downloads": 8,
                        "noplaylist": True,
                        "postprocessors": [],
                        "http_headers": {"User-Agent": "Mozilla/5.0"},
                        "extractor_args": {"youtube": {"player_client": ["web", "ios", "android"]}},
                    }
                # For X URLs, check if it's a GIF before download (to convert to .gif after)
                convert_to_gif = False
                if is_x_url(url) and ffmpeg_bin:
                    try:
                        probe_opts = {"noplaylist": True, "quiet": True}
                        with yt_dlp.YoutubeDL(probe_opts) as ydl:
                            convert_to_gif = is_likely_x_gif(ydl, url)
                    except Exception:
                        pass

                # Capture actual output path from yt-dlp (X/Twitter may use different naming)
                downloaded_paths = []

                def progress_hook(d):
                    if d.get("status") == "finished":
                        info = d.get("info_dict") or {}
                        path = info.get("_filename")
                        if path and os.path.isfile(path):
                            ext = (Path(path).suffix or "").lower()
                            if ext != ".m4a":  # exclude extracted audio only
                                downloaded_paths.append(path)

                ydl_opts["progress_hooks"] = [progress_hook]

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                # Find the downloaded file: prefer yt-dlp's actual path, else our expected path
                saved_mp4 = str(VIDEO_DIR / f"{job_id}.mp4")
                saved_audio_m4a = str(VIDEO_DIR / f"{job_id}.m4a")
                global video_path
                video_path = None
                if downloaded_paths:
                    # Use actual path from yt-dlp (handles X/Twitter naming quirks)
                    p = Path(downloaded_paths[-1]).resolve()
                    try:
                        p.relative_to(BASE_DIR)
                        video_path = str(p)
                    except ValueError:
                        pass  # path outside BASE_DIR, use fallback
                if not video_path and os.path.exists(saved_mp4):
                    video_path = saved_mp4
                if not video_path:
                    # Fallback: look for any downloaded video with our job_id
                    candidates = list(VIDEO_DIR.glob(f"{job_id}.*"))
                    video_path = str(candidates[0]) if candidates else None
                if video_path:
                    print(f"[+] Video saved: {video_path}")
                    if not TRANSCRIBE_ENABLED:
                        with lock:
                            data["transcription"] = url

                # Convert X GIFs from mp4 to actual .gif
                if convert_to_gif and video_path and ffmpeg_bin:
                    gif_path = str(VIDEO_DIR / f"{job_id}.gif")
                    original_video = video_path
                    if convert_mp4_to_gif(ffmpeg_bin, video_path, gif_path):
                        video_path = gif_path
                        try:
                            os.remove(original_video)
                            print(f"[+] Converted to GIF, removed original: {original_video}")
                        except OSError as e:
                            print(f"[+] Converted to GIF: {video_path} (could not remove original: {e})")
                    else:
                        print(f"[-] GIF conversion failed, keeping mp4")

                # Move audio to AUDIO_DIR when transcribing (videos stay in VIDEO_DIR)
                if TRANSCRIBE_ENABLED and os.path.exists(saved_audio_m4a):
                    dest_m4a = str(AUDIO_DIR / f"{job_id}.m4a")
                    shutil.move(saved_audio_m4a, dest_m4a)
                    saved_audio_m4a = dest_m4a
                    print(f"[+] Audio saved: {dest_m4a}")

                if TRANSCRIBE_ENABLED:
                    # Lazy-load Whisper on first use (speeds up startup)
                    if model is None:
                        print("[+] Loading Whisper model (first transcription)...")
                        import whisper
                        model = whisper.load_model("base", device="cpu")
                    # Transcribe downloaded media (prefer extracted m4a for speed)
                    source_for_transcription = saved_audio_m4a if os.path.exists(saved_audio_m4a) else video_path
                    if not source_for_transcription or not os.path.exists(source_for_transcription):
                        raise FileNotFoundError("No media file was downloaded")
                    print(f"[+] Transcribing: {source_for_transcription}")
                    result = model.transcribe(source_for_transcription, fp16=False)
                    transcription = (result.get("text") or "").strip()
                    if not transcription:
                        transcription = "..."

                    # Update transcription to server immediately (so shortcut gets it even if file write fails)
                    with lock:
                        data["transcription"] = transcription
                    print(f"[+] Transcription completed: {transcription[:50]}{'...' if len(transcription) > 50 else ''}")

                    # Save transcription to a file in the same folder as this script
                    output_path = os.path.join(script_dir, "transcription.txt")
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(transcription)
                    print(f"[+] Transcription saved to: {output_path}")

                last_processed_key = url_key

            except Exception as e:
                print(f"[-] Error occurred: {e}")
                traceback.print_exc()
                last_processed_key = url_key  # avoid endless retries on same URL
                with lock:
                    if not data.get("transcription"):
                        data["transcription"] = "..."

        time.sleep(5)

def start_flask():
    # Run Flask server in a background thread without the reloader
    app.run(host='0.0.0.0', port=5000, use_reloader=False)

def start_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox
    # Simple Tkinter UI to submit a URL and watch transcription status
    root = tk.Tk()
    root.title("TikTok Downloader & Transcriber")

    url_var = tk.StringVar()
    status_var = tk.StringVar(value="Idle")
    last_text = {"value": ""}
    last_video_url = {"value": ""}

    container = ttk.Frame(root, padding=12)
    container.pack(fill=tk.BOTH, expand=True)

    ttk.Label(container, text="Paste video URL:").pack(anchor=tk.W)
    entry = ttk.Entry(container, textvariable=url_var, width=70)
    entry.pack(fill=tk.X)
    entry.focus_set()

    def submit_url():
        url = url_var.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please paste a video URL.")
            return
        try:
            requests.post('http://127.0.0.1:5000/set_url', json={"url": url}, timeout=5)
        except Exception:
            # Fallback: set in-memory if local server not yet reachable
            global video_path
            with lock:
                data["url"] = url
                data["transcription"] = ""
                video_path = None
        status_var.set("Submitted. Processing...")

    ttk.Button(container, text="Submit", command=submit_url).pack(anchor=tk.W, pady=(6, 10))

    ttk.Label(container, textvariable=status_var).pack(anchor=tk.W)
    ttk.Label(container, text="Transcription:").pack(anchor=tk.W, pady=(10, 2))

    transcript = tk.Text(container, height=10, wrap=tk.WORD)
    transcript.pack(fill=tk.BOTH, expand=True)
    transcript.configure(state=tk.DISABLED)

    def set_transcript_text(text):
        transcript.configure(state=tk.NORMAL)
        transcript.delete("1.0", tk.END)
        transcript.insert(tk.END, text)
        transcript.configure(state=tk.DISABLED)

    link_frame = ttk.Frame(container)
    link_frame.pack(fill=tk.X, pady=(8, 0))
    video_btn = ttk.Button(link_frame, text="Open Downloaded Video", state=tk.DISABLED)
    video_btn.pack(anchor=tk.W)

    def open_video():
        if last_video_url["value"]:
            try:
                webbrowser.open(last_video_url["value"])  # open in default browser
            except Exception:
                pass

    video_btn.configure(command=open_video)

    def poll():
        try:
            resp = requests.get('http://127.0.0.1:5000/get_transcription', timeout=5)
            if resp.ok:
                payload = resp.json()
                text = payload.get("transcription", "") or "..."
                video_url = payload.get("video_url", "")
                if text != last_text["value"]:
                    last_text["value"] = text
                    set_transcript_text(text)
                    # Update status based on whether we're still waiting
                    if text.strip() and text.strip() != "...":
                        status_var.set("Completed")
                    else:
                        status_var.set("Processing...")
                if video_url != last_video_url["value"]:
                    last_video_url["value"] = video_url
                    video_btn.configure(state=(tk.NORMAL if video_url else tk.DISABLED))
        except Exception:
            # Server might not be up yet
            pass
        finally:
            root.after(3000, poll)

    root.after(1000, poll)
    root.mainloop()

if __name__ == '__main__':
    TRANSCRIBE_ENABLED = prompt_transcribe_choice()
    globals()["TRANSCRIBE_ENABLED"] = TRANSCRIBE_ENABLED
    print(f"  Mode: {'Transcribe' if TRANSCRIBE_ENABLED else 'Download only'}\n")

    # Start Flask first so server accepts connections ASAP
    server_thread = Thread(target=start_flask, daemon=True)
    server_thread.start()

    # Start background worker (ffmpeg/whisper load lazily on first URL)
    worker_thread = Thread(target=worker, daemon=True)
    worker_thread.start()

    # Start the desktop GUI for local input
    start_gui()
