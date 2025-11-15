#!/usr/bin/env python3
"""
smallpie backend v0.5 – Meeting server

- Accepts WebSocket audio streams from the frontend and turns them into meeting transcripts + analysis.
- Accepts uploaded audio files via HTTP and processes them the same way.
- Uses local whisper.cpp (whisper-cli) for transcription with chunking.
- Uses GPT-5.1 for meeting analysis and trait extraction.
"""

import os
import sys
import uuid
import time
import queue
import random
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import Thread

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

# ============================================================
# CONFIG
# ============================================================

WHISPER_CLI = "/root/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL = "/root/whisper.cpp/models/ggml-large-v3-q5_0.bin"

CHUNK_SECONDS = 60  
WHISPER_THREADS = 6

BASE_DIR = Path("/root/smallpie-data").resolve()
AUDIO_DIR = BASE_DIR / "audio"
MEETINGS_DIR = BASE_DIR / "meetings"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
MEETINGS_DIR.mkdir(parents=True, exist_ok=True)

TRAITS_FILE = BASE_DIR / "damyan_traits.txt"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ============================================================
# HELPERS
# ============================================================

def rand_delay(label: str = ""):
    d = random.uniform(1.5, 4.0)
    print(f"[delay] {label}: sleeping {d:.2f}s")
    time.sleep(d)

def run_ffprobe_duration(path: Path) -> float:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ]
        ).decode().strip()
        return float(out)
    except:
        return 0.0

def convert_to_wav(src_path: Path) -> Path:
    dst = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src_path), "-ac", "1", "-ar", "16000", str(dst)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return dst

def slice_wav_to_chunks(wav_path: Path, chunk_seconds: int) -> list[Path]:
    duration = run_ffprobe_duration(wav_path)
    if duration == 0.0:
        return [wav_path]

    chunks = []
    start = 0.0
    idx = 1

    while start < duration:
        end = min(start + chunk_seconds, duration)
        chunk_path = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name)

        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(wav_path),
                "-ss", str(start),
                "-to", str(end),
                "-acodec", "copy",
                str(chunk_path),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        chunks.append(chunk_path)
        idx += 1
        start = end

    return chunks

def _transcribe_single_chunk(chunk_path: Path) -> str:
    out_prefix = Path(tempfile.NamedTemporaryFile(delete=False).name)

    subprocess.run(
        [
            WHISPER_CLI,
            "-m", WHISPER_MODEL,
            "-f", str(chunk_path),
            "-otxt",
            "-of", str(out_prefix),
            "-t", str(WHISPER_THREADS),
            "-l", "auto",
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )

    txt_candidate = out_prefix.with_suffix(".txt")
    try:
        return txt_candidate.read_text(encoding="utf-8").strip()
    except:
        return ""

def transcribe_with_whisper_local(audio_file: Path) -> str:
    wav_path = convert_to_wav(audio_file)
    chunks = slice_wav_to_chunks(wav_path, CHUNK_SECONDS)

    parts = []
    for chunk in chunks:
        text = _transcribe_single_chunk(chunk)
        parts.append(text)
        chunk.unlink(missing_ok=True)

    try:
        wav_path.unlink()
    except:
        pass

    return "\n\n".join(p for p in parts if p.strip())

def analyze_with_gpt(meeting_name: str, meeting_topic: str, participants: str, transcript: str) -> str:
    rand_delay("before GPT analysis")

    prompt = f"""
You are an expert meeting analyst.
Meeting name: {meeting_name}
Topic: {meeting_topic}
Participants: {participants}

--- TRANSCRIPT START ---
{transcript}
--- TRANSCRIPT END ---
"""

    resp = client.responses.create(model="gpt-5.1", input=prompt)
    return resp.output_text.strip()

def update_traits(transcript: str, analysis: str):
    rand_delay("before traits")

    trait_prompt = f"""
TRANSCRIPT:
{transcript}

ANALYSIS:
{analysis}
"""

    resp = client.responses.create(model="gpt-5.1", input=trait_prompt)
    traits = resp.output_text.strip()

    with TRAITS_FILE.open("a", encoding="utf-8") as f:
        f.write("\n\n==== NEW SESSION ====\n")
        f.write(traits)

def save_meeting_outputs(meeting_id: str, meeting_name: str, transcript: str, analysis: str) -> Path:
    safe = meeting_name.replace(" ", "_")
    folder = MEETINGS_DIR / f"meeting_{meeting_id}_{safe}"
    folder.mkdir(parents=True, exist_ok=True)

    (folder / "transcript.txt").write_text(transcript, encoding="utf-8")
    (folder / "analysis.txt").write_text(analysis, encoding="utf-8")
    return folder

def full_meeting_pipeline(audio_path: Path, meeting_name: str, meeting_topic: str, participants: str, meeting_id: str):
    transcript = transcribe_with_whisper_local(audio_path)
    analysis = analyze_with_gpt(meeting_name, meeting_topic, participants, transcript)
    save_meeting_outputs(meeting_id, meeting_name, transcript, analysis)
    update_traits(transcript, analysis)

# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(title="smallpie backend", version="0.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/meetings/upload")
async def upload_meeting_file(
    meeting_name: str = Form(...),
    meeting_topic: str = Form(...),
    participants: str = Form(...),
    file: UploadFile = File(...),
):
    meeting_id = uuid.uuid4().hex
    raw_path = AUDIO_DIR / f"{meeting_id}{Path(file.filename).suffix}"

    with raw_path.open("wb") as f:
        while True:
            chunk = await file.read(1024*1024)
            if not chunk:
                break
            f.write(chunk)

    def _run():
        full_meeting_pipeline(raw_path, meeting_name, meeting_topic, participants, meeting_id)

    Thread(target=_run, daemon=True).start()

    return {"status": "accepted", "meeting_id": meeting_id}

# ============================================================
# ORIGINAL STREAMING WS ENDPOINT (RESTORED)
# ============================================================

@app.websocket("/ws/record")
async def websocket_record(websocket: WebSocket):
    await websocket.accept()

    qp = websocket.query_params
    meeting_name = qp.get("meeting_name", "Untitled meeting")
    meeting_topic = qp.get("meeting_topic", "Not specified")
    participants = qp.get("participants", "Not specified")

    meeting_id = uuid.uuid4().hex
    raw_path = AUDIO_DIR / f"{meeting_id}.webm"

    print(f"[ws] new recording session meeting_id={meeting_id}")
    print(f"[ws] {meeting_name=} {meeting_topic=} {participants=}")

    with raw_path.open("ab") as f:
        try:
            while True:
                msg = await websocket.receive()
                if "bytes" in msg and msg["bytes"] is not None:
                    f.write(msg["bytes"])
                elif "text" in msg and msg["text"] == "STOP":
                    print("[ws] received STOP")
                    break
        except WebSocketDisconnect:
            print("[ws] client disconnected")

    print(f"[ws] stored streamed audio at {raw_path}")

    def _run():
        full_meeting_pipeline(raw_path, meeting_name, meeting_topic, participants, meeting_id)

    Thread(target=_run, daemon=True).start()

    try:
        await websocket.close()
    except:
        pass

# ============================================================
# CLI ENTRYPOINT
# ============================================================

def cli_main():
    if len(sys.argv) < 2:
        print("Usage: python meeting_server.py <audio_file> [meeting_name] [meeting_topic] [participants]")
        sys.exit(1)

    audio_path = Path(sys.argv[1]).resolve()
    meeting_name = sys.argv[2] if len(sys.argv) > 2 else "CLI test meeting"
    meeting_topic = sys.argv[3] if len(sys.argv) > 3 else "CLI topic"
    participants = sys.argv[4] if len(sys.argv) > 4 else "CLI participants"

    full_meeting_pipeline(audio_path, meeting_name, meeting_topic, participants, uuid.uuid4().hex)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith((".wav", ".mp3", ".webm", ".m4a", ".aac", ".ogg")):
        cli_main()
    else:
        print("Run with uvicorn:")
        print("  uvicorn meeting_server:app --host 0.0.0.0 --port 8000")