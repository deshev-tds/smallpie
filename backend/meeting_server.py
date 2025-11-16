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
import threading # Added for live transcription

import smtplib
from email.message import EmailMessage

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

# === FIX 1: ADD SEMAPHORE ===
# Limit concurrent whisper-cli processes to 1 to prevent CPU starvation.
# On an 8-core machine, 1 process using 6 threads is the max safe load.
WHISPER_SEMAPHORE = threading.Semaphore(1)
print(f"[config] Whisper concurrency limit set to 1 (using {WHISPER_THREADS} threads per job)")
# ============================


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
# EMAIL / SMTP CONFIG
# ============================================================

SMTP_HOST = os.getenv("SMALLPIE_SMTP_HOST")
SMTP_PORT = int(os.getenv("SMALLPIE_SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMALLPIE_SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMALLPIE_SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMALLPIE_SMTP_FROM") or SMTP_USERNAME or "no-reply@smallpie.local"

EMAIL_ENABLED = bool(SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD)
if not EMAIL_ENABLED:
    print("[email] SMTP not fully configured; email sending is disabled")

# ============================================================
# SIMPLE BEARER TOKEN AUTH
# ============================================================

ACCESS_TOKEN = os.getenv("SMALLPIE_ACCESS_TOKEN", "").strip()
AUTH_ENABLED = bool(ACCESS_TOKEN)

if AUTH_ENABLED:
    print("[auth] Bearer token auth ENABLED for HTTP + WS")
else:
    print("[auth] Bearer token auth DISABLED (SMALLPIE_ACCESS_TOKEN not set)")


def verify_bearer_token(authorization: str | None):
    """
    Enforce Authorization: Bearer <token> for HTTP endpoints
    when SMALLPIE_ACCESS_TOKEN is set. Otherwise, it's a no-op.
    """
    if not AUTH_ENABLED:
        return

    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    parts = authorization.split()
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
        
        # === FIX 2: Handle 'N/A' from ffprobe ===
        if out == "N/A":
            print(f"[ffprobe] duration 'N/A' for {path} (likely empty/corrupt segment)", file=sys.stderr)
            return 0.0
        # =======================================

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
        # This now gracefully handles the N/A case from run_ffprobe_duration
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
    
    === FIX 1: This function now blocks on WHISPER_SEMAPHORE ===
    """
    
    print(f"[whisper] waiting for semaphore to run on: {chunk_path}")
    with WHISPER_SEMAPHORE:
        print(f"[whisper] semaphore ACQUIRED, running on chunk: {chunk_path}")
        
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

        # This subprocess.run will now only happen one at a time
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
        
        print(f"[whisper] semaphore RELEASED for chunk: {chunk_path}")
        return text
    # =========================================================


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
        # This call will now block until the semaphore is free
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

**Pattern:** A short, evidence-based description of a recurring behavior or cognitive style.  
**Implications:** A practical guideline for AI systems collaborating with him.

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


def send_analysis_via_email(
    recipient: str | None,
    meeting_name: str,
    meeting_id: str,
    folder: Path,
) -> None:
    """
    If recipient and SMTP config are available, email the analysis (and optionally transcript)
    to the user. Best-effort only: never raise out of here.
    """
    if not recipient:
        return

    if not EMAIL_ENABLED:
        print("[email] EMAIL_ENABLED is False; skipping email send for", meeting_id)
        return

    try:
        transcript_path = folder / "transcript.txt"
        analysis_path = folder / "analysis.txt"

        transcript = ""
        analysis = ""

        if analysis_path.exists():
            try:
                analysis = analysis_path.read_text(encoding="utf-8")
            except Exception as e:
                print(f"[email] failed to read analysis.txt for {meeting_id}: {e}", file=sys.stderr)

        if transcript_path.exists():
            try:
                transcript = transcript_path.read_text(encoding="utf-8")
            except Exception as e:
                print(f"[email] failed to read transcript.txt for {meeting_id}: {e}", file=sys.stderr)

        msg = EmailMessage()
        msg["Subject"] = f"[smallpie] Notes for '{meeting_name}'"
        msg["From"] = SMTP_FROM
        msg["To"] = recipient

        parts: list[str] = []
        parts.append(f"Here are your smallpie notes for meeting '{meeting_name}' (ID: {meeting_id}).")
        parts.append("")
        if analysis:
            parts.append("=== ANALYSIS ===")
            parts.append(analysis)
            parts.append("")
        if transcript:
            parts.append("=== TRANSCRIPT (may be truncated) ===")
            if len(transcript) > 15000:
                parts.append(transcript[:15000])
                parts.append("\n[transcript truncated]")
            else:
                parts.append(transcript)

        msg.set_content("\n".join(parts))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            if SMTP_USERNAME and SMTP_PASSWORD:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(
                    msg,
                    from_addr=SMTP_FROM,          # <= envelope FROM FIX
                    to_addrs=[recipient],         # <= explicit TO
                )

        print(f"[email] sent meeting {meeting_id} to {recipient}")
    except Exception as e:
        print(f"[email] failed to send email for meeting {meeting_id}: {e}", file=sys.stderr)


def full_meeting_pipeline(
    audio_path: Path,
    meeting_name: str,
    meeting_topic: str,
    participants: str,
    meeting_id: str | None = None,
    user_email: str | None = None,
):

    if meeting_id is None:
        meeting_id = uuid.uuid4().hex

    print(f"[pipeline] starting full pipeline for meeting_id={meeting_id}")

    transcript = transcribe_with_whisper_local(audio_path)
    analysis = analyze_with_gpt(meeting_name, meeting_topic, participants, transcript)
    folder = save_meeting_outputs(meeting_id, meeting_name, transcript, analysis)
    update_traits(transcript, analysis)

    # Best-effort email delivery (does not affect pipeline success)
    try:
        send_analysis_via_email(user_email, meeting_name, meeting_id, folder)
    except Exception as e:
        print(f"[email] unexpected exception in full_meeting_pipeline for {meeting_id}: {e}", file=sys.stderr)

    print(f"[pipeline] meeting {meeting_id} complete, stored at {folder}")


# ============================================================
# LIVE TRANSCRIPTION ADDITIONS (V2 - POLLING)
# ============================================================

class ThreadSafeTranscript:
    """
    Safely collects transcript parts from multiple worker threads
    and ensures they are stored in the correct order.
    """
    def __init__(self):
        self.parts: dict[int, str] = {}
        self.lock = threading.Lock()

    def add(self, index: int, text: str):
        """Adds a transcript part from a chunk at a specific index."""
        with self.lock:
            self.parts[index] = text
            print(f"[pipeline-live] stored transcript for chunk {index}")

    def get_full_transcript(self) -> str:
        """Assembles the final transcript in order."""
        with self.lock:
            # Sort by chunk index (the dict key) and join
            sorted_parts = [self.parts[k] for k in sorted(self.parts.keys())]
            return "\n\n".join(p for p in sorted_parts if p.strip())


def extract_wav_segment(raw_path: Path, start_sec: float, end_sec: float | None) -> Path | None:
    """
    Uses ffmpeg to extract a segment from the main webm file.
    Returns the path to the extracted WAV segment, or None on failure.
    """
    if not raw_path.exists() or raw_path.stat().st_size == 0:
        print("[ffmpeg-extract] raw file not ready")
        return None

    dst = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name)
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(raw_path),
        "-ss", str(start_sec),
    ]
    if end_sec is not None:
        cmd.extend(["-to", str(end_sec)])
    
    cmd.extend([
        "-ac", "1",
        "-ar", "16000",
        str(dst),
    ])

    print(f"[ffmpeg-extract] running: {cmd}")
    try:
        # We use check_output to wait and catch errors
        subprocess.check_output(cmd, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        print(f"[ffmpeg-extract] failed to extract {start_sec}-{end_sec}: {e.stderr.decode()}", file=sys.stderr)
        try:
            dst.unlink()
        except FileNotFoundError:
            pass
        return None

    if dst.exists() and dst.stat().st_size > 44: # 44 bytes = empty WAV header
        return dst
    else:
        print(f"[ffmpeg-extract] output file is empty for {start_sec}-{end_sec}")
        try:
            dst.unlink()
        except FileNotFoundError:
            pass
        return None


def process_wav_chunk_thread(
    wav_path: Path,
    chunk_index: int,
    transcript_store: ThreadSafeTranscript
):
    """
    A single worker thread's target.
    Takes one *WAV* chunk, processes it, and stores the text.
    """
    try:
        print(f"[pipeline-live] worker starting for WAV chunk {chunk_index} ({wav_path})")

        # 1. Slice this ~60s WAV into *whisper* sub-chunks
        # (This is fast, just ffmpeg copying)
        sub_chunks = slice_wav_to_chunks(wav_path, CHUNK_SECONDS)
        print(f"[pipeline-live] chunk {chunk_index} split into {len(sub_chunks)} whisper sub-chunks")

        # 2. Transcribe each sub-chunk
        parts: list[str] = []
        for idx, sub_chunk in enumerate(sub_chunks, start=1):
            # This call will now block on the global WHISPER_SEMAPHORE
            text = _transcribe_single_chunk(sub_chunk)
            parts.append(text)
            try:
                sub_chunk.unlink()
            except FileNotFoundError:
                pass

        # 3. Clean up temp WAV
        try:
            wav_path.unlink()
        except FileNotFoundError:
            pass

        # 4. Store the final text for this chunk
        chunk_transcript = "\n\n".join(p for p in parts if p.strip())
        transcript_store.add(chunk_index, chunk_transcript)
        print(f"[pipeline-live] worker completed for chunk {chunk_index}")

    except Exception as e:
        print(f"[pipeline-live] FATAL ERROR processing chunk {chunk_index}: {e}", file=sys.stderr)
        transcript_store.add(chunk_index, f"[[ERROR: Failed to transcribe chunk {chunk_index}]]")


def live_transcription_orchestrator(
    raw_path: Path,
    recording_stopped: threading.Event,
    transcript_store: ThreadSafeTranscript,
    meeting_id: str,
    meeting_name: str,
    meeting_topic: str,
    participants: str,
    user_email: str | None
):
    """
    The main background thread for a live session.
    - Polls the main audio file every CHUNK_SECONDS.
    - Extracts WAV segments.
    - Spawns transcription threads (which will queue via semaphore).
    - When signaled, runs the final GPT/save pipeline.
    """
    chunk_index = 0
    processing_threads: list[threading.Thread] = []
    
    try:
        while True:
            # Wait for CHUNK_SECONDS, or until the recording stops
            # wait() returns True if the event was set, False on timeout
            was_stopped = recording_stopped.wait(timeout=CHUNK_SECONDS)

            start_sec = chunk_index * CHUNK_SECONDS
            end_sec = (chunk_index + 1) * CHUNK_SECONDS

            # Try to extract the segment
            # We add a small delay to let ffmpeg catch up if the file is new
            if chunk_index == 0:
                time.sleep(0.5) 
            
            wav_chunk_path = extract_wav_segment(raw_path, start_sec, end_sec)

            if wav_chunk_path:
                print(f"[orchestrator] successfully extracted segment {chunk_index} ({start_sec}s-{end_sec}s)")
                t = threading.Thread(
                    target=process_wav_chunk_thread,
                    args=(wav_chunk_path, chunk_index, transcript_store),
                    daemon=True
                )
                t.start()
                processing_threads.append(t)
                chunk_index += 1
            else:
                print(f"[orchestrator] failed to extract segment {chunk_index} (file might be too short? recording_stopped={was_stopped})")

            if was_stopped:
                print("[orchestrator] recording stopped, breaking poll loop")
                break
        
        # --- Recording has stopped, process the final segment ---
        print("[orchestrator] processing final audio segment...")
        start_sec = chunk_index * CHUNK_SECONDS
        
        # Give a moment for the file to be fully flushed
        time.sleep(0.5) 
        
        # Extract from the last chunk start to the very end (end_sec=None)
        wav_chunk_path = extract_wav_segment(raw_path, start_sec, None)
        
        if wav_chunk_path:
            print(f"[orchestrator] successfully extracted final segment {chunk_index} (from {start_sec}s to end)")
            t = threading.Thread(
                target=process_wav_chunk_thread,
                args=(wav_chunk_path, chunk_index, transcript_store),
                daemon=True
            )
            t.start()
            processing_threads.append(t)
        else:
            print(f"[orchestrator] no final segment to process (from {start_sec}s)")

        # --- Wait for all transcription threads and run analysis ---
        print(f"[orchestrator] waiting for {len(processing_threads)} chunk(s) to finish... (queue is managed by semaphore)")
        for t in processing_threads:
            t.join()

        print("[orchestrator] all chunks processed.")

        # 1. Get the final, ordered transcript
        transcript = transcript_store.get_full_transcript()
        print(f"[orchestrator] final transcript length: {len(transcript)}")

        if not transcript:
            print("[orchestrator] empty transcript, skipping GPT analysis")
            return

        # 2. Run the rest of the *original* pipeline
        analysis = analyze_with_gpt(meeting_name, meeting_topic, participants, transcript)
        folder = save_meeting_outputs(meeting_id, meeting_name, transcript, analysis)
        update_traits(transcript, analysis)

        # 3. Send email (best-effort)
        try:
            send_analysis_via_email(user_email, meeting_name, meeting_id, folder)
        except Exception as e:
            print(f"[email] unexpected exception in orchestrator for {meeting_id}: {e}", file=sys.stderr)

        print(f"[orchestrator] meeting {meeting_id} complete, stored at {folder}")

    except Exception as e:
        print(f"[orchestrator] FATAL ERROR for meeting {meeting_id}: {e}", file=sys.stderr)
        try:
            transcript = transcript_store.get_full_transcript()
            if transcript:
                save_meeting_outputs(meeting_id, f"FAILED_{meeting_name}", transcript, f"ANALYSIS FAILED:\n{e}")
        except Exception as save_e:
            print(f"[orchestrator] failed to save error state: {save_e}", file=sys.stderr)
    finally:
        # Clean up the large raw .webm file
        try:
            raw_path.unlink()
            print(f"[orchestrator] cleaned up {raw_path}")
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[orchestrator] failed to clean up {raw_path}: {e}", file=sys.stderr)


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
    user_email: str | None = Form(default=None),
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
            full_meeting_pipeline(raw_path, meeting_name, meeting_topic, participants, meeting_id, user_email=user_email)
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

    # Auth via ?token=...
    qp = websocket.query_params
    ws_token = qp.get("token")
    if not verify_ws_token(ws_token):
        await websocket.close(code=1008)
        return

    await websocket.accept()

    # --- Metadata Setup ---
    meeting_name = qp.get("meeting_name", "Untitled meeting")
    meeting_topic = qp.get("meeting_topic", "Not specified")
    participants = qp.get("participants", "Not specified")
    user_email = qp.get("user_email")
    meeting_id = uuid.uuid4().hex
    first_message_processed = False

    print(f"[ws] new recording session meeting_id={meeting_id}")

    # --- Live Processing Setup ---
    # We write to ONE file, and the orchestrator reads from it.
    raw_path = AUDIO_DIR / f"{meeting_id}.webm"
    recording_stopped = threading.Event()
    transcript_store = ThreadSafeTranscript()

    # Start the orchestrator thread. It will wait for the file.
    orchestrator = threading.Thread(
        target=live_transcription_orchestrator,
        args=(
            raw_path,
            recording_stopped,
            transcript_store,
            meeting_id,
            meeting_name,
            meeting_topic,
            participants,
            user_email
        ),
        daemon=True
    )
    
    # We must handle metadata *before* starting the thread,
    # or pass the metadata to the thread *after* we receive it.
    # Easiest is to parse metadata first, then launch.
    
    # Let's adjust: we'll parse the *first* message for metadata,
    # then launch the orchestrator, then start writing audio.
    
    try:
        # --- Wait for First Message (Metadata) ---
        print("[ws] waiting for metadata message...")
        msg = await websocket.receive()

        if msg.get("type") == "websocket.disconnect":
            print("[ws] client disconnected before metadata")
            return
        
        if "text" in msg and msg["text"] is not None:
            first_message_processed = True
            text = msg["text"].strip()
            try:
                meta = json.loads(text)
                if isinstance(meta, dict) and meta.get("type") == "metadata":
                    meeting_name = meta.get("meeting_name", meeting_name)
                    meeting_topic = meta.get("meeting_topic", meeting_topic)
                    participants = meta.get("participants", participants)
                    user_email = meta.get("user_email", user_email)
                    print("[ws] metadata received:", meta)
                else:
                    print("[ws] first message not metadata, using defaults")
            except Exception as e:
                print(f"[ws] metadata parse error '{text}', using defaults: {e}")
        else:
            print("[ws] first message was not text, using defaults")
            # If first message is binary, we can't 'un-receive' it.
            # This logic assumes metadata *always* comes first.
            # We'll just have to write the binary if that's what we got.
            pass # We'll handle the binary in the main loop

        # --- Now, start the orchestrator ---
        print(f"[ws] resolved: name={meeting_name} topic={meeting_topic}")
        orchestrator_args = (
            raw_path, recording_stopped, transcript_store,
            meeting_id, meeting_name, meeting_topic, participants, user_email
        )
        orchestrator = threading.Thread(
            target=live_transcription_orchestrator,
            args=orchestrator_args,
            daemon=True
        )
        orchestrator.start()
        print("[ws] live transcription orchestrator started")

        # --- Main Audio Loop ---
        with raw_path.open("ab") as f:
            
            # If the first message was binary, write it now
            if "bytes" in msg and msg["bytes"] is not None:
                f.write(msg["bytes"])

            while True:
                msg = await websocket.receive()

                if msg.get("type") == "websocket.disconnect":
                    print("[ws] websocket.disconnect received")
                    break

                # Binary audio chunk
                if "bytes" in msg and msg["bytes"] is not None:
                    f.write(msg["bytes"])
                    continue

                # Text message (STOP / END / noise)
                if "text" in msg and msg["text"] is not None:
                    text = msg["text"].strip()
                    
                    # STOP detection (JSON)
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict) and parsed.get("type", "").lower() == "end":
                            print("[ws] received stop marker (json)")
                            break
                    except Exception:
                        pass

                    # STOP detection (plain text)
                    upper = text.upper()
                    if upper in ("STOP", "END"):
                        print(f"[ws] received stop marker: {upper}")
                        break
                    
                    # We ignore any other text (e.g., late metadata)
                    print("[ws] ignoring text message:", repr(text))
                    continue

    except WebSocketDisconnect:
        print("[ws] client disconnected")
    except Exception as e:
        print(f"[ws] error while receiving audio: {e}", file=sys.stderr)
    finally:
        # --- Finalize and Hand-off ---
        # By exiting the 'with' block, the file `f` is closed and flushed.
        print(f"[ws] stored streamed audio at {raw_path}")
        
        # Now we signal the orchestrator that the recording is finished
        # and the file is ready for final processing.
        recording_stopped.set()
        print("[ws] 'recording_stopped' event set for orchestrator")

        # Try to close cleanly
        try:
            await websocket.close()
        except RuntimeError:
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
        print(f"File not found: {audio_chupath}")
        sys.exit(1)

    meeting_name = sys.argv[2] if len(sys.argv) > 2 else "CLI test meeting"
    meeting_topic = sys.argv[3] if len(sys.argv) > 3 else "CLI topic"
    participants = sys.argv[4] if len(sys.argv) > 4 else "CLI participants"

    full_meeting_pipeline(audio_path, meeting_name, meeting_topic, participants, None, user_email=None)


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