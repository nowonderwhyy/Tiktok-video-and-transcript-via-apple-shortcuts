from flask import Flask, request, jsonify, send_from_directory
from threading import Thread, Lock
import requests
import yt_dlp
import whisper
import os
import time
from pathlib import Path
import uuid

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
                    "format": "mp4/best",
                    "merge_output_format": "mp4",
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                # Update global video_path to the saved mp4
                saved_mp4 = str(VIDEO_DIR / f"{job_id}.mp4")
                global video_path
                video_path = saved_mp4

                # Transcribe downloaded video
                result = model.transcribe(saved_mp4)
                transcription = result["text"]

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

        time.sleep(5)

if __name__ == '__main__':
    # Start worker thread
    thread = Thread(target=worker, daemon=True)
    thread.start()

    # IMPORTANT: Use host='0.0.0.0' so other devices on LAN can connect.
    # Then, from your iPhone, access: http://YOURHOSTNAME.local:5000
    app.run(host='0.0.0.0', port=5000)
