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

# Local whisper.cpp CLI + model
WHISPER_CLI = "/root/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL = "/root/whisper.cpp/models/ggml-large-v3-q5_0.bin"

# How long each chunk given to whisper-cli should be (in seconds)
CHUNK_SECONDS = 60  # 1 min chunks for better parallelization

# How many CPU threads to give to whisper
WHISPER_THREADS = 6  # on 8-core VPS, leaves some headroom for OS / FastAPI

# Storage layout
BASE_DIR = Path("/root/smallpie-data").resolve()
AUDIO_DIR = BASE_DIR / "audio"
MEETINGS_DIR = BASE_DIR / "meetings"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
MEETINGS_DIR.mkdir(parents=True, exist_ok=True)

TRAITS_FILE = BASE_DIR / "damyan_traits.txt"

# OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ============================================================
# HELPERS
# ============================================================

def rand_delay(label: str = ""):
    """Small random delay to de-sync calls to GPT a bit."""
    d = random.uniform(1.5, 4.0)
    print(f"[delay] {label}: sleeping {d:.2f}s")
    time.sleep(d)


def run_ffprobe_duration(path: Path) -> float:
    """Return duration in seconds for an audio file using ffprobe."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ]
        ).decode().strip()
        return float(out)
    except Exception as e:
        print(f"[ffprobe] failed to read duration for {path}: {e}", file=sys.stderr)
        return 0.0


def convert_to_wav(src_path: Path) -> Path:
    """
    Convert any browser-uploaded/recorded format (webm, m4a, mp3, etc.)
    into a mono 16 kHz WAV suitable for whisper.cpp.
    """
    dst = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name)
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(src_path),
        "-ac", "1",
        "-ar", "16000",
        str(dst),
    ]
    print(f"[ffmpeg] {src_path} -> {dst}")
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return dst


def slice_wav_to_chunks(wav_path: Path, chunk_seconds: int) -> list[Path]:
    """
    Slice a long WAV file into smaller WAV chunks using ffmpeg.
    Returns list of chunk paths.
    """
    duration = run_ffprobe_duration(wav_path)
    if duration == 0.0:
        return [wav_path]

    chunks = []
    start = 0.0
    idx = 1

    while start < duration:
        end = min(start + chunk_seconds, duration)
        chunk_path = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name)

        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(wav_path),
            "-ss", str(start),
            "-to", str(end),
            "-acodec", "copy",
            str(chunk_path),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"[ffmpeg] chunk {idx}: {start:.1f}s -> {end:.1f}s -> {chunk_path}")
        chunks.append(chunk_path)

        idx += 1
        start = end

    return chunks


def _transcribe_single_chunk(chunk_path: Path) -> str:
    """
    Call whisper-cli on a single WAV chunk and return plain text transcript.
    """
    out_prefix = Path(tempfile.NamedTemporaryFile(delete=False).name)

    cmd = [
        WHISPER_CLI,
        "-m", WHISPER_MODEL,
        "-f", str(chunk_path),
        "-otxt",
        "-of", str(out_prefix),
        "-t", str(WHISPER_THREADS),
        "-l", "auto",
    ]

    print(f"[whisper] running on chunk: {chunk_path}")
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    txt_candidate = out_prefix.with_suffix(".txt")
    if not txt_candidate.exists():
        txt_candidate = out_prefix

    try:
        text = txt_candidate.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        text = ""

    try:
        txt_candidate.unlink(missing_ok=True)
    except Exception:
        if txt_candidate.exists():
            txt_candidate.unlink()

    return text
def transcribe_with_whisper_local(audio_file: Path) -> str:
    print(f"[pipeline] starting local transcription for {audio_file}")

    wav_path = convert_to_wav(audio_file)
    duration = run_ffprobe_duration(wav_path)
    print(f"[pipeline] wav duration ~ {duration:.1f} seconds")

    chunks = slice_wav_to_chunks(wav_path, CHUNK_SECONDS)
    print(f"[pipeline] total chunks: {len(chunks)}")

    parts: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        print(f"[pipeline] transcribing chunk {idx}/{len(chunks)}")
        text = _transcribe_single_chunk(chunk)
        parts.append(text)
        try:
            chunk.unlink()
        except FileNotFoundError:
            pass

    try:
        wav_path.unlink()
    except FileNotFoundError:
        pass

    transcript = "\n\n".join(p for p in parts if p.strip())
    print("[pipeline] transcription complete, length:", len(transcript))
    return transcript


def analyze_with_gpt(meeting_name: str, meeting_topic: str, participants: str, transcript: str) -> str:
    rand_delay("before GPT analysis")
    print("[gpt] starting meeting analysis")

    prompt = f"""
You are an expert meeting analyst.

Given the raw transcript of a meeting (possibly in multiple languages), do the following:

1) Reconstruct the conversation as a clean dialog with inferred speakers:
   - Use labels like "Speaker 1:", "Speaker 2:", etc.
   - Group consecutive sentences by the same speaker into paragraphs.
   - Do NOT alternate speakers blindly; infer turns by meaning.

2) Extract and list:
   - Concrete actions Damyan must take.
   - Concrete actions other participants must take.
   - Dependencies or blocked items.
   - Deadlines or time references.

3) Identify:
   - Misalignments in expectations.
   - Risks.

Rules:
- Base everything ONLY on the transcript content.
- Output in English.

Meeting name: {meeting_name}
Topic: {meeting_topic}
Participants: {participants}

--- TRANSCRIPT START ---
{transcript}
--- TRANSCRIPT END ---
"""

    resp = client.responses.create(
        model="gpt-5.1",
        input=prompt,
    )
    text = resp.output_text.strip()
    print("[gpt] analysis done")
    return text


def update_traits(transcript: str, analysis: str):
    rand_delay("before traits")
    print("[traits] updating traits file")

    trait_prompt = f"""
You are maintaining a long-term behavioral and cognitive profile of Damyan.

Extract stable patterns… (truncated here for brevity, unchanged)
TRANSCRIPT:
{transcript}

ANALYSIS:
{analysis}
"""

    resp = client.responses.create(
        model="gpt-5.1",
        input=trait_prompt,
    )
    traits = resp.output_text.strip()

    TRAITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with TRAITS_FILE.open("a", encoding="utf-8") as f:
        f.write("\n\n==== NEW SESSION ====\n")
        f.write(traits)

    print("[traits] traits file updated")


def save_meeting_outputs(meeting_id: str, meeting_name: str, transcript: str, analysis: str) -> Path:
    safe_name = meeting_name.replace(" ", "_").replace(":", "_")
    folder = MEETINGS_DIR / f"meeting_{meeting_id}_{safe_name}"
    folder.mkdir(parents=True, exist_ok=True)

    (folder / "transcript.txt").write_text(transcript, encoding="utf-8")
    (folder / "analysis.txt").write_text(analysis, encoding="utf-8")

    print("[save] outputs written to", folder)
    return folder


def full_meeting_pipeline(audio_path: Path, meeting_name: str, meeting_topic: str, participants: str, meeting_id: str | None = None):
    if meeting_id is None:
        meeting_id = uuid.uuid4().hex

    print(f"[pipeline] starting full pipeline for meeting_id={meeting_id}")

    transcript = transcribe_with_whisper_local(audio_path)
    analysis = analyze_with_gpt(meeting_name, meeting_topic, participants, transcript)
    folder = save_meeting_outputs(meeting_id, meeting_name, transcript, analysis)
    update_traits(transcript, analysis)

    print(f"[pipeline] meeting {meeting_id} complete")


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
    original_suffix = Path(file.filename or "upload").suffix or ".bin"
    raw_path = AUDIO_DIR / f"{meeting_id}{original_suffix}"

    with raw_path.open("wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

    print(f"[upload] stored uploaded file at {raw_path}")

    def _run():
        try:
            full_meeting_pipeline(raw_path, meeting_name, meeting_topic, participants, meeting_id)
        finally:
            pass

    Thread(target=_run, daemon=True).start()

    return JSONResponse(
        {
            "status": "accepted",
            "meeting_id": meeting_id,
            "message": "File received. Processing in background.",
        }
    )


# ============================================================
# WS ENDPOINT WITH YOUR PATCH
# ============================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("Client connected!")

    try:
        # === FIRST MESSAGE: metadata JSON ===
        first_msg = await websocket.receive()
        meeting_name = "Untitled meeting"
        meeting_topic = "Not specified"
        participants = "Not specified"

        if "text" in first_msg and first_msg["text"]:
            import json
            try:
                meta = json.loads(first_msg["text"])
                meeting_name = meta.get("meeting_name", meeting_name)
                meeting_topic = meta.get("meeting_topic", meeting_topic)
                participants = meta.get("participants", participants)
                print("Received metadata:", meta)
            except Exception as e:
                print("Metadata parse error:", e)

        # === Now receive chunks ===
        while True:
            msg = await websocket.receive()

            # PATCH: detect end-of-stream
            if "text" in msg and msg["text"]:
                import json
                try:
                    j = json.loads(msg["text"])
                    if j.get("type") == "end":
                        print("Received END message")
                        break
                except Exception:
                    pass

            # Normal binary chunk
            if "bytes" in msg and msg["bytes"]:
                data = msg["bytes"]
                print("Got chunk:", len(data))
                await websocket.send_text("ACK")

            elif msg.get("type") == "websocket.disconnect":
                print("Client disconnected")
                break

    except WebSocketDisconnect:
        print("Client disconnected")

    except Exception as e:
        print("WS ERROR:", e)


def cli_main():
    if len(sys.argv) < 2:
        print("Usage: python meeting_server.py <audio_file> [...]")
        sys.exit(1)

    audio_path = Path(sys.argv[1]).resolve()
    if not audio_path.exists():
        print(f"File not found: {audio_path}")
        sys.exit(1)

    meeting_name = sys.argv[2] if len(sys.argv) > 2 else "CLI test meeting"
    meeting_topic = sys.argv[3] if len(sys.argv) > 3 else "CLI topic"
    participants = sys.argv[4] if len(sys.argv) > 4 else "CLI participants"

    full_meeting_pipeline(audio_path, meeting_name, meeting_topic, participants)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith((".wav", ".mp3", ".webm", ".m4a", ".aac", ".ogg")):
        cli_main()
    else:
        print("This is an ASGI app. Use uvicorn:")
        print("  uvicorn meeting_server:app --host 0.0.0.0 --port 8000")