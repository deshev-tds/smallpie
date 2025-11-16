#!/usr/bin/env python3

import os
import sys
import uuid
import time
import queue
import random
import shutil
import subprocess
import tempfile
import json
from pathlib import Path
from threading import Thread

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Header, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI



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
# SIMPLE BEARER TOKEN AUTH
# ============================================================

ACCESS_TOKEN = os.getenv("SMALLPIE_ACCESS_TOKEN")
AUTH_ENABLED = ACCESS_TOKEN is not None and ACCESS_TOKEN != ""

if not AUTH_ENABLED:
    print("[auth] WARNING: SMALLPIE_ACCESS_TOKEN not set. Authentication is DISABLED.")
else:
    print("[auth] Authentication ENABLED using SMALLPIE_ACCESS_TOKEN.")


def verify_bearer_token(authorization_header: str | None) -> None:
    """
    Verify the Authorization: Bearer <token> header for HTTP endpoints.
    If AUTH_ENABLED is False, this is a no-op.
    """
    if not AUTH_ENABLED:
        return

    if not authorization_header:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")

    token = parts[1].strip()
    if token != ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid access token")


def verify_ws_token(token: str | None) -> bool:
    """
    Verify the ?token=... query parameter for WebSocket connections.
    Returns True if accepted, False if rejected.
    """
    if not AUTH_ENABLED:
        return True

    if not token:
        print("[auth] WebSocket missing token")
        return False

    if token != ACCESS_TOKEN:
        print("[auth] WebSocket invalid token")
        return False

    return True


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
    Keeps inference local on the VPS.
    """
    # whisper-cli expects an output prefix; we use a temp prefix path
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
        # fallback: maybe CLI wrote directly to prefix without .txt
        txt_candidate = out_prefix

    try:
        text = txt_candidate.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        text = ""

    # cleanup
    try:
        txt_candidate.unlink(missing_ok=True)
    except TypeError:
        # Python < 3.8 compatibility if ever
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

Given the raw transcript of a meeting (possibly in multiple languages, with multiple participants), do the following:

1) Reconstruct the conversation as a clean dialog with inferred speakers:
   - Use labels like "Speaker 1:", "Speaker 2:", etc.
   - Group consecutive sentences by the same speaker into paragraphs.
   - Do NOT alternate speakers blindly; infer turns by meaning.

2) Extract and list:
   - Concrete actions Damyan must take.
   - Concrete actions other participants must take.
   - Dependencies or blocked items (who/what they depend on).
   - Deadlines or time references, if present.

3) Identify:
   - Misalignments in expectations.
   - Risks (technical, process, interpersonal).

Rules:
- Base everything ONLY on the transcript content.
- If something is implied but not explicit, mark it as "inferred".
- Output must be in English, even if the transcript is not.

Meeting name: {meeting_name}
Topic: {meeting_topic}
Participants (count or description): {participants}

--- TRANSCRIPT START ---
{transcript}
--- TRANSCRIPT END ---
"""

    resp = client.responses.create(
        model="gpt-5.1",
        input=prompt,
    )
    text = resp.output_text.strip()
    print("[gpt] analysis done, length:", len(text))
    return text


def update_traits(transcript: str, analysis: str):

    rand_delay("before traits")
    print("[traits] updating traits file")

    trait_prompt = f"""
You are maintaining a long-term professional behavioral and cognitive profiles of participants in a meeting.

Your goal is NOT to describe their personality in general terms, but to extract stable,
recurring patterns of thinking, communication, decision-making, and collaboration
that appear across this specific meeting transcript.

These traits must:
- be grounded ONLY in evidence from the transcript + analysis
- describe patterns, not one-off moments
- be phrased as practical insights that future AI assistants can use to work with them effectively
- avoid psychological diagnoses or speculation
- avoid praise, value-judgments, or flattery
- avoid overgeneralizing beyond the evidence

Produce up to 5 bullet points, each written as:

**Pattern:**  
A short, evidence-based description of a recurring behavior or cognitive style.  
**Implications:**  
A practical guideline for AI systems collaborating with him.

Example format (not content):
- **Pattern:** Tends to organize information linearly when uncertain.  
  **Implications:** Provide responses with clear sequencing and minimal ambiguity.

After producing the 5 bullet points, generate a second independent
version of the same 5 points using a different internal reasoning path.
Then compute a "self-consistency score" for each point:

Score 1–5:
1 = the two versions diverge strongly
5 = the two versions describe the same pattern

Return the final bullet points with their self-consistency scores. 

For each bullet point, add a "Stability Score" (1–5):
1 = possibly situational or one-off
5 = highly likely to be a recurring pattern across multiple future meetings

Use this exact style.

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
    print("[traits] traits file updated at", TRAITS_FILE)


def save_meeting_outputs(meeting_id: str, meeting_name: str, transcript: str, analysis: str) -> Path:
    """
    Save transcript + analysis under MEETINGS_DIR/meeting_<id>/.
    Returns folder path.
    """
    safe_name = meeting_name.replace(" ", "_").replace(":", "_")
    folder = MEETINGS_DIR / f"meeting_{meeting_id}_{safe_name}"
    folder.mkdir(parents=True, exist_ok=True)

    (folder / "transcript.txt").write_text(transcript, encoding="utf-8")
    (folder / "analysis.txt").write_text(analysis, encoding="utf-8")

    print("[save] outputs written to", folder)
    return folder


def full_meeting_pipeline(
    audio_path: Path,
    meeting_name: str,
    meeting_topic: str,
    participants: str,
    meeting_id: str | None = None,
):

    if meeting_id is None:
        meeting_id = uuid.uuid4().hex

    print(f"[pipeline] starting full pipeline for meeting_id={meeting_id}")

    transcript = transcribe_with_whisper_local(audio_path)
    analysis = analyze_with_gpt(meeting_name, meeting_topic, participants, transcript)
    folder = save_meeting_outputs(meeting_id, meeting_name, transcript, analysis)
    update_traits(transcript, analysis)

    print(f"[pipeline] meeting {meeting_id} complete, stored at {folder}")


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(title="smallpie backend", version="0.5.0")

# CORS: allow your domains; for dev we just open it up a bit
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for MVP; lock this down later
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
    authorization: str | None = Header(default=None),
):
    # HTTP Bearer auth (only enforced if SMALLPIE_ACCESS_TOKEN is set)
    verify_bearer_token(authorization)

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

    # Run the heavy lifting in a thread
    def _run():
        try:
            full_meeting_pipeline(raw_path, meeting_name, meeting_topic, participants, meeting_id)
        finally:
            # you can decide here if you want to keep the original audio or not
            pass

    Thread(target=_run, daemon=True).start()

    return JSONResponse(
        {
            "status": "accepted",
            "meeting_id": meeting_id,
            "message": "File received. Processing will continue in the background.",
        }
    )


# ============================================================
# ACTIVE WS ENDPOINT: METADATA + BINARY CHUNKS + STOP/END
# ============================================================

@app.websocket("/ws")
async def websocket_record(websocket: WebSocket):
 
    # Auth via ?token=... in query params (only enforced if SMALLPIE_ACCESS_TOKEN is set)
    qp = websocket.query_params
    ws_token = qp.get("token")
    if not verify_ws_token(ws_token):
        # Close with policy violation
        await websocket.close(code=1008)
        return

    await websocket.accept()

    # Defaults
    meeting_name = qp.get("meeting_name", "Untitled meeting")
    meeting_topic = qp.get("meeting_topic", "Not specified")
    participants = qp.get("participants", "Not specified")

    meeting_id = uuid.uuid4().hex
    raw_path = AUDIO_DIR / f"{meeting_id}.webm"

    print(f"[ws] new recording session meeting_id={meeting_id}")
    print(f"[ws] initial (from query): name={meeting_name} topic={meeting_topic} participants={participants}")

    first_message_processed = False

    with raw_path.open("ab") as f:
        try:
            while True:
                msg = await websocket.receive()

                # WebSocket disconnected event from Starlette
                if msg.get("type") == "websocket.disconnect":
                    print("[ws] websocket.disconnect received")
                    break

                # Binary audio chunk
                if "bytes" in msg and msg["bytes"] is not None:
                    f.write(msg["bytes"])
                    # optional ACK; currently silent
                    continue

                # Text message (metadata / STOP / END / noise)
                if "text" in msg and msg["text"] is not None:
                    text = msg["text"].strip()
                    if not first_message_processed:
                        # Try to parse metadata JSON on the first text message
                        first_message_processed = True
                        try:
                            meta = json.loads(text)
                            if isinstance(meta, dict) and meta.get("type") == "metadata":
                                meeting_name = meta.get("meeting_name", meeting_name)
                                meeting_topic = meta.get("meeting_topic", meeting_topic)
                                participants = meta.get("participants", participants)
                                print("[ws] metadata received:", meta)
                                print(f"[ws] resolved: name={meeting_name} topic={meeting_topic} participants={participants}")
                                continue
                        except Exception as e:
                            print("[ws] metadata parse error:", e)

                    # Stop markers
                    upper = text.upper()
                    if upper in ("STOP", "END"):
                        print(f"[ws] received stop marker: {upper}")
                        break

                    # Any other text is ignored
                    print("[ws] ignoring text message:", repr(text))
                    continue

                # Any other kind of message is ignored
        except WebSocketDisconnect:
            print("[ws] client disconnected")
        except Exception as e:
            print(f"[ws] error while receiving audio: {e}", file=sys.stderr)

    print(f"[ws] stored streamed audio at {raw_path}")

    # Start the heavy pipeline in a background thread
    def _run():
        try:
            full_meeting_pipeline(raw_path, meeting_name, meeting_topic, participants, meeting_id)
        finally:
            # decide whether to delete raw_path or keep history
            pass

    Thread(target=_run, daemon=True).start()

    # Try to close cleanly
    try:
        await websocket.close()
    except RuntimeError:
        # already closed
        pass


# ============================================================
# CLI ENTRY POINT FOR MANUAL TESTING
# ============================================================

def cli_main():

    if len(sys.argv) < 2:
        print("Usage: python meeting_server.py <audio_file> [meeting_name] [meeting_topic] [participants]")
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
    # If you want to run the FastAPI app directly:
    #   uvicorn meeting_server:app --host 0.0.0.0 --port 8000
    #
    # But for convenience, if launched with an argument, treat it as CLI mode:
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(
        (".wav", ".mp3", ".webm", ".m4a", ".aac", ".ogg")
    ):
        cli_main()
    else:
        print("This module is intended to be run with uvicorn as an ASGI app, e.g.:")
        print("  uvicorn meeting_server:app --host 0.0.0.0 --port 8000")