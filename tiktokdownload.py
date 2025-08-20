from flask import Flask, request, jsonify, send_from_directory
from threading import Thread, Lock
import requests
import yt_dlp
import whisper
import os
import time
from pathlib import Path
import uuid
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser

# Server configuration
app = Flask(__name__, static_folder="static", static_url_path="/static")
data = {"url": "", "transcription": ""}
lock = Lock()
script_dir = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = Path(__file__).parent
VIDEO_DIR = BASE_DIR / "static" / "videos"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

# Path to saved mp4 for current job
video_path = None

@app.route('/set_url', methods=['POST'])
def set_url():
    content = request.json
    with lock:
        data["url"] = content.get('url', '')
        data["transcription"] = ""
        # reset video path for new job
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
    last_processed_url = ""
    model = whisper.load_model("base")

    while True:
        with lock:
            url = data["url"]

        if url and url != last_processed_url:
            print(f"[+] Processing new URL: {url}")

            try:
                # Download new video into static/videos with a unique id
                job_id = uuid.uuid4().hex
                outtmpl = str(VIDEO_DIR / f"{job_id}.%(ext)s")
                ydl_opts = {
                    "outtmpl": outtmpl,
                    # Best video + best audio, fallback to best single file
                    "format": "bv*+ba/best",

                    # Prefer ffmpeg for HLS (avoids flaky hlsnative)
                    "hls_prefer_native": False,

                    # Reliability
                    "skip_unavailable_fragments": True,
                    "fragment_retries": 20,
                    "retries": 10,
                    "concurrent_fragment_downloads": 8,
                    "noplaylist": True,

                    # Keep the merged MP4 even after extracting audio
                    "keepvideo": True,

                    # Postprocess/merge to MP4 (requires ffmpeg in PATH)
                    "postprocessors": [
                        {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
                        {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "0"},
                    ],
                    "merge_output_format": "mp4",

                    # Help YouTube extraction
                    "http_headers": {"User-Agent": "Mozilla/5.0"},
                    "extractor_args": {"youtube": {"player_client": ["web", "ios", "android"]}},
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                # Update global video_path to the saved mp4
                saved_mp4 = str(VIDEO_DIR / f"{job_id}.mp4")
                saved_audio_m4a = str(VIDEO_DIR / f"{job_id}.m4a")
                global video_path
                if os.path.exists(saved_mp4):
                    video_path = saved_mp4
                else:
                    video_path = None

                # Transcribe downloaded media (prefer extracted m4a for speed)
                source_for_transcription = saved_audio_m4a if os.path.exists(saved_audio_m4a) else saved_mp4
                result = model.transcribe(source_for_transcription)
                transcription = result["text"]
                if not transcription or not transcription.strip():
                    transcription = "..."

                # Save transcription to a file in the same folder as this script
                output_path = os.path.join(script_dir, "transcription.txt")
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(transcription)
                print(f"[+] Transcription saved to: {output_path}")

                # Update transcription to server
                with lock:
                    data["transcription"] = transcription
                    print(f"[+] Transcription completed: {transcription[:30]}...")

                last_processed_url = url

            except Exception as e:
                print(f"[-] Error occurred: {e}")
                with lock:
                    if not data.get("transcription"):
                        data["transcription"] = "..."

        time.sleep(5)

def start_flask():
    # Run Flask server in a background thread without the reloader
    app.run(host='0.0.0.0', port=5000, use_reloader=False)

def start_gui():
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
    # Start background worker
    thread = Thread(target=worker, daemon=True)
    thread.start()

    # Run Flask server in background so GUI can run in the main thread
    server_thread = Thread(target=start_flask, daemon=True)
    server_thread.start()

    # Start the desktop GUI for local input
    start_gui()
