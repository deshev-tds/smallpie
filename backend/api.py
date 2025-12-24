import json
import queue
import threading
import uuid
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

try:
    from . import config  # type: ignore
    from .auth import verify_bearer_token, verify_ws_token  # type: ignore
    from .pipeline import (  # type: ignore
        ThreadSafeTranscript,
        live_transcription_orchestrator,
        start_full_pipeline_in_thread,
    )
except ImportError:
    import config  # type: ignore
    from auth import verify_bearer_token, verify_ws_token  # type: ignore
    from pipeline import (  # type: ignore
        ThreadSafeTranscript,
        live_transcription_orchestrator,
        start_full_pipeline_in_thread,
    )

app = FastAPI(title="smallpie backend", version="0.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOW_ORIGINS,
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
    verify_bearer_token(authorization)

    meeting_id = uuid.uuid4().hex
    original_suffix = Path(file.filename or "upload").suffix or ".bin"
    raw_path = config.AUDIO_DIR / f"{meeting_id}{original_suffix}"

    with raw_path.open("wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

    print(f"[upload] stored uploaded file at {raw_path}")

    start_full_pipeline_in_thread(raw_path, meeting_name, meeting_topic, participants, meeting_id, user_email=user_email)

    return JSONResponse(
        {
            "status": "accepted",
            "meeting_id": meeting_id,
            "message": "File received. Processing will continue in the background.",
        }
    )


@app.websocket("/ws")
async def websocket_record(websocket: WebSocket):
    qp = websocket.query_params
    ws_token = qp.get("token")
    if not verify_ws_token(ws_token):
        await websocket.close(code=1008)
        return

    await websocket.accept()

    meeting_name = qp.get("meeting_name", "Untitled meeting")
    meeting_topic = qp.get("meeting_topic", "Not specified")
    participants = qp.get("participants", "Not specified")
    user_email = qp.get("user_email")
    meeting_id = uuid.uuid4().hex

    print(f"[ws] new recording session meeting_id={meeting_id}")

    data_queue = queue.Queue()
    recording_stopped = threading.Event()
    transcript_store = ThreadSafeTranscript()

    try:
        print("[ws] waiting for metadata message...")
        msg = await websocket.receive()

        if msg.get("type") == "websocket.disconnect":
            print("[ws] client disconnected before metadata")
            return

        if "text" in msg and msg["text"] is not None:
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
            if "bytes" in msg and msg["bytes"] is not None:
                data_queue.put(msg["bytes"])

        print(f"[ws] resolved: name={meeting_name} topic={meeting_topic}")
        orchestrator = threading.Thread(
            target=live_transcription_orchestrator,
            args=(
                data_queue,
                recording_stopped,
                transcript_store,
                meeting_id,
                meeting_name,
                meeting_topic,
                participants,
                user_email,
            ),
            daemon=True,
        )
        orchestrator.start()
        print("[ws] live transcription orchestrator started")

        while True:
            msg = await websocket.receive()

            if msg.get("type") == "websocket.disconnect":
                print("[ws] websocket.disconnect received")
                break

            if "bytes" in msg and msg["bytes"] is not None:
                data_queue.put(msg["bytes"])
                continue

            if "text" in msg and msg["text"] is not None:
                text = msg["text"].strip()

                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict) and parsed.get("type", "").lower() == "end":
                        print("[ws] received stop marker (json)")
                        break
                except Exception:
                    pass

                upper = text.upper()
                if upper in ("STOP", "END"):
                    print(f"[ws] received stop marker: {upper}")
                    break

                print("[ws] ignoring text message:", repr(text))
                continue

    except WebSocketDisconnect:
        print("[ws] client disconnected")
    except Exception as e:
        print(f"[ws] error while receiving audio: {e}", file=sys.stderr)
    finally:
        print(f"[ws] client disconnected, signaling orchestrator to stop")
        recording_stopped.set()

        try:
            await websocket.close()
        except RuntimeError:
            pass
