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
    print(f"[ws] initial (from query): name={meeting_name} topic={meeting_topic} participants={participants}")

    first_message_processed = False

    # NEW: per-segment state
    segment_index = 0
    segment_frame_count = 0
    segment_path = AUDIO_DIR / f"{meeting_id}_seg{segment_index:03d}.webm"
    segment_file = segment_path.open("ab")

    # For collecting transcripts as they arrive
    segment_results: dict[int, str] = {}
    segment_lock = Lock()
    segment_threads: list[Thread] = []

    try:
        while True:
            msg = await websocket.receive()

            if "bytes" in msg and msg["bytes"] is not None:
                # Write to current segment
                segment_file.write(msg["bytes"])
                segment_frame_count += 1

                # Have we reached ~CHUNK_SECONDS of audio in this segment?
                if segment_frame_count * WS_FRAME_SECONDS >= CHUNK_SECONDS:
                    # Close this segment and start transcription
                    segment_file.close()
                    print(f"[ws] segment {segment_index} closed at ~{segment_frame_count * WS_FRAME_SECONDS:.1f}s: {segment_path}")

                    t = Thread(
                        target=transcribe_segment_async,
                        args=(segment_index, segment_path, segment_results, segment_lock),
                        daemon=True,
                    )
                    t.start()
                    segment_threads.append(t)

                    # Start next segment
                    segment_index += 1
                    segment_frame_count = 0
                    segment_path = AUDIO_DIR / f"{meeting_id}_seg{segment_index:03d}.webm"
                    segment_file = segment_path.open("ab")

                continue

            if "text" in msg and msg["text"] is not None:
                text = msg["text"].strip()

                # FIRST TEXT MESSAGE → metadata
                if not first_message_processed:
                    first_message_processed = True
                    try:
                        meta = json.loads(text)
                        if isinstance(meta, dict) and meta.get("type") == "metadata":
                            meeting_name = meta.get("meeting_name", meeting_name)
                            meeting_topic = meta.get("meeting_topic", meeting_topic)
                            participants = meta.get("participants", participants)
                            user_email = meta.get("user_email", user_email)
                            print("[ws] metadata received:", meta)
                            print(f"[ws] resolved: name={meeting_name} topic={meeting_topic} participants={participants}")
                            continue
                    except Exception as e:
                        print("[ws] metadata parse error:", e)

                # STOP detection (JSON or plain text) – unchanged
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
        # Finalize last segment if it has data
        try:
            if not segment_file.closed:
                segment_file.close()
        except Exception:
            pass

        # If we actually wrote something into this segment, transcribe it too
        if segment_frame_count > 0 and segment_path.exists():
            print(f"[ws] final partial segment {segment_index} closed with ~{segment_frame_count * WS_FRAME_SECONDS:.1f}s")
            t = Thread(
                target=transcribe_segment_async,
                args=(segment_index, segment_path, segment_results, segment_lock),
                daemon=True,
            )
            t.start()
            segment_threads.append(t)

    print(f"[ws] all segments scheduled for transcription for meeting_id={meeting_id}")

    # Now aggregate everything & run GPT in a background thread
    def _run_after_stream():
        # Wait for all whisper work to finish
        for t in segment_threads:
            t.join()

        # Build final transcript in order
        ordered_indices = sorted(segment_results.keys())
        transcript_parts = [segment_results[i] for i in ordered_indices]
        transcript = "\n\n".join(p for p in transcript_parts if p.strip())

        print("[pipeline] streaming transcription complete, total length:", len(transcript))

        run_analysis_and_save(
            meeting_id=meeting_id,
            meeting_name=meeting_name,
            meeting_topic=meeting_topic,
            participants=participants,
            transcript=transcript,
            user_email=user_email,
        )

    Thread(target=_run_after_stream, daemon=True).start()

    try:
        await websocket.close()
    except RuntimeError:
        pass
