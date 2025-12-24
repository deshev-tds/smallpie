import json
import queue
import threading
import uuid
import sys
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, UploadFile, WebSocket, WebSocketDisconnect, Request, HTTPException, status
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
    from .tokens import issue_token, validate_token, revoke_session, revoke_token_by_jti  # type: ignore
except ImportError:
    import config  # type: ignore
    from auth import verify_bearer_token, verify_ws_token  # type: ignore
    from pipeline import (  # type: ignore
        ThreadSafeTranscript,
        live_transcription_orchestrator,
        start_full_pipeline_in_thread,
    )
    from tokens import issue_token, validate_token, revoke_session, revoke_token_by_jti  # type: ignore

app = FastAPI(title="smallpie backend", version="0.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/api/token")
async def issue_session_token(
    request: Request,
    authorization: str | None = Header(default=None),
    bootstrap_header: str | None = Header(default=None, alias="x-bootstrap-token"),
    scope: str = Form(...),
    session_id: str | None = Form(default=None),
):
    """
    Issues a short-lived, scoped session token.
    Protected by a simple bootstrap secret + rate limiting.
    """
    client_id = request.client.host if request.client else "unknown"

    if not config.BOOTSTRAP_SECRET:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Token issuer not configured")

    auth_value = authorization or bootstrap_header

    if not auth_value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bootstrap token")

    supplied = auth_value
    if authorization:
        if not authorization.lower().startswith("bearer"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bootstrap token")
        parts = authorization.split(None, 1)
        if len(parts) < 2:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bootstrap token")
        supplied = parts[1].strip()

    if supplied != config.BOOTSTRAP_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bootstrap token")

    if scope not in {"ws", "upload"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid scope")

    issued = issue_token(scope, session_id, client_id)
    return JSONResponse(issued)


@app.post("/api/meetings/upload")
async def upload_meeting_file(
    request: Request,
    meeting_name: str = Form(...),
    meeting_topic: str = Form(...),
    participants: str = Form(...),
    file: UploadFile = File(...),
    user_email: str | None = Form(default=None),
    authorization: str | None = Header(default=None),
):
    client_host = request.client.host if request and request.client else "unknown"
    authed = False
    token_payload = None

    if authorization and authorization.lower().startswith("bearer "):
        token_value = authorization.split(None, 1)[1].strip()
        try:
            token_payload = validate_token(token_value, "upload", client_host)
            authed = True
            revoke_token_by_jti(token_payload.get("jti", ""))
        except HTTPException:
            authed = False

    if not authed:
        # Fallback to legacy static token auth
        verify_bearer_token(authorization)
        authed = True

    meeting_id = token_payload["session_id"] if token_payload else uuid.uuid4().hex
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

    if token_payload:
        revoke_session(token_payload["session_id"])

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
    client_host = websocket.client.host if websocket.client else "unknown"
    token_payload = None

    if ws_token:
        try:
            token_payload = validate_token(ws_token, "ws", client_host)
            # One-shot: consume the token so it cannot be reused
            revoke_token_by_jti(token_payload.get("jti", ""))
        except HTTPException:
            token_payload = None

    if token_payload is None:
        # Fallback to legacy static token
        if not verify_ws_token(ws_token):
            await websocket.close(code=1008)
            return

    await websocket.accept()

    meeting_name = qp.get("meeting_name", "Untitled meeting")
    meeting_topic = qp.get("meeting_topic", "Not specified")
    participants = qp.get("participants", "Not specified")
    user_email = qp.get("user_email")
    meeting_id = token_payload["session_id"] if token_payload else uuid.uuid4().hex

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
        if token_payload:
            revoke_session(token_payload["session_id"])
        print(f"[ws] client disconnected, signaling orchestrator to stop")
        recording_stopped.set()

        try:
            await websocket.close()
        except RuntimeError:
            pass
