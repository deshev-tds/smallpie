import queue
import subprocess
import sys
import tempfile
import threading
import uuid
import time
from pathlib import Path
from threading import Thread

try:
    from . import config  # type: ignore
    from .analysis import analyze_with_gpt  # type: ignore
    from .audio import convert_to_wav, transcribe_wav_file  # type: ignore
    from .emailer import send_analysis_via_email  # type: ignore
    from .storage import save_meeting_outputs, cleanup_meeting_folder  # type: ignore
except ImportError:
    import config  # type: ignore
    from analysis import analyze_with_gpt  # type: ignore
    from audio import convert_to_wav, transcribe_wav_file  # type: ignore
    from emailer import send_analysis_via_email  # type: ignore
    from storage import save_meeting_outputs, cleanup_meeting_folder  # type: ignore


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
            sorted_parts = [self.parts[k] for k in sorted(self.parts.keys())]
            return "\n\n".join(p for p in sorted_parts if p.strip())


def process_wav_chunk_thread(
    wav_chunk_path: Path,
    chunk_index: int,
    transcript_store: ThreadSafeTranscript,
):
    """
    A single worker thread's target.
    Takes one *WAV* chunk, processes it, and stores the text.
    """
    try:
        print(f"[pipeline-live] worker starting for WAV chunk {chunk_index} ({wav_chunk_path})")

        chunk_transcript = transcribe_wav_file(wav_chunk_path)
        transcript_store.add(chunk_index, chunk_transcript)
        print(f"[pipeline-live] worker completed for chunk {chunk_index}")

    except Exception as e:
        print(f"[pipeline-live] FATAL ERROR processing chunk {chunk_index}: {e}", file=sys.stderr)
        transcript_store.add(chunk_index, f"[[ERROR: Failed to transcribe chunk {chunk_index}]]")


def build_and_extract_wav_chunk(
    part_files: list[Path],
    start_sec: float,
    duration_sec: float | None,
    chunk_index: int,
) -> Path | None:
    """
    1. Binary-appends all part_files into a single .webm stream.
    2. Uses ffmpeg to seek, convert, and extract the desired WAV chunk.
    3. Cleans up the large .webm stream file.
    """
    full_stream_path = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".webm").name)
    try:
        print(f"[orchestrator] building full stream for chunk {chunk_index} from {len(part_files)} parts...")
        with full_stream_path.open("wb") as f_out:
            for part_path in part_files:
                f_out.write(part_path.read_bytes())

        wav_chunk_path = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name)

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(full_stream_path),
            "-ss",
            str(start_sec),
        ]
        if duration_sec is not None:
            cmd.extend(["-t", str(duration_sec)])

        cmd.extend(
            [
                "-ac",
                "1",
                "-ar",
                "16000",
                str(wav_chunk_path),
            ]
        )

        print(f"[orchestrator] extracting chunk {chunk_index}: {' '.join(cmd)}")
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        if wav_chunk_path.exists() and wav_chunk_path.stat().st_size > 44:
            return wav_chunk_path
        else:
            print(f"[orchestrator] extraction for chunk {chunk_index} produced empty file")
            try:
                wav_chunk_path.unlink()
            except FileNotFoundError:
                pass
            return None

    except subprocess.CalledProcessError as e:
        print(
            f"[orchestrator] FATAL: ffmpeg extraction failed for chunk {chunk_index}: {e.stderr.decode()}",
            file=sys.stderr,
        )
        return None
    except Exception as e:
        print(f"[orchestrator] FATAL: build_and_extract failed for chunk {chunk_index}: {e}", file=sys.stderr)
        return None
    finally:
        try:
            full_stream_path.unlink()
        except FileNotFoundError:
            pass


def extraction_and_transcription_thread(
    part_files: list[Path],
    start_sec: float,
    duration_sec: float | None,
    chunk_index: int,
    transcript_store: ThreadSafeTranscript,
):
    """
    Thread target that builds/extracts a chunk and transcribes it.
    """
    try:
        wav_chunk = build_and_extract_wav_chunk(part_files, start_sec, duration_sec, chunk_index)

        if wav_chunk:
            process_wav_chunk_thread(wav_chunk, chunk_index, transcript_store)
        else:
            print(f"[orchestrator] skipping transcription for chunk {chunk_index}, extraction failed.")
    except Exception as e:
        print(f"[orchestrator] FATAL unhandled error in worker thread for chunk {chunk_index}: {e}", file=sys.stderr)
        transcript_store.add(chunk_index, f"[[ERROR: Worker thread failed for chunk {chunk_index}]]")


def full_meeting_pipeline(
    audio_path: Path,
    meeting_name: str,
    meeting_topic: str,
    participants: str,
    meeting_id: str | None = None,
    user_email: str | None = None,
):
    """Pipeline for file uploads."""
    if meeting_id is None:
        meeting_id = uuid.uuid4().hex

    print(f"[pipeline-upload] starting full pipeline for meeting_id={meeting_id}")

    folder: Path | None = None
    try:
        try:
            wav_path = convert_to_wav(audio_path)
        except subprocess.CalledProcessError as e:
            print(
                f"[pipeline-upload] FATAL: convert_to_wav failed for {audio_path}: {e.stderr.decode()}",
                file=sys.stderr,
            )
            return

        if not wav_path.exists() or wav_path.stat().st_size == 0:
            print(f"[pipeline-upload] conversion failed for {audio_path}")
            return

        transcript = transcribe_wav_file(wav_path)

        if not transcript.strip():
            print(f"[pipeline-upload] empty transcript for {meeting_id}, aborting")
            return

        analysis = analyze_with_gpt(meeting_name, meeting_topic, participants, transcript)
        folder = save_meeting_outputs(meeting_id, meeting_name, transcript, analysis)

        try:
            send_analysis_via_email(user_email, meeting_name, meeting_id, folder)
        except Exception as e:
            print(f"[email] unexpected exception in full_meeting_pipeline for {meeting_id}: {e}", file=sys.stderr)

        print(f"[pipeline-upload] meeting {meeting_id} complete, stored at {folder}")
    finally:
        if folder:
            cleanup_meeting_folder(folder)


def live_transcription_orchestrator(
    data_queue: queue.Queue,
    recording_stopped: threading.Event,
    transcript_store: ThreadSafeTranscript,
    meeting_id: str,
    meeting_name: str,
    meeting_topic: str,
    participants: str,
    user_email: str | None,
):
    """
    Background thread for a live session.
    """
    folder: Path | None = None
    chunk_index = 0
    processing_threads: list[threading.Thread] = []

    all_part_files: list[Path] = []
    part_index = 0
    chunk_start_time = time.time()

    try:
        while True:
            try:
                blob = data_queue.get(timeout=0.5)
                part_path = config.AUDIO_DIR / f"{meeting_id}_part_{part_index:04d}.webm"
                part_path.write_bytes(blob)
                all_part_files.append(part_path)
                part_index += 1
            except queue.Empty:
                pass

            now = time.time()
            is_stopped = recording_stopped.is_set()

            if (now - chunk_start_time > config.CHUNK_SECONDS) and not is_stopped:
                print(f"[orchestrator] {config.CHUNK_SECONDS}s passed, cutting chunk {chunk_index}")

                if all_part_files:
                    start_sec = chunk_index * config.CHUNK_SECONDS

                    t = threading.Thread(
                        target=extraction_and_transcription_thread,
                        args=(
                            list(all_part_files),
                            start_sec,
                            config.CHUNK_SECONDS,
                            chunk_index,
                            transcript_store,
                        ),
                        daemon=True,
                    )
                    t.start()
                    processing_threads.append(t)
                else:
                    print(f"[orchestrator] timer fired but no parts to process for chunk {chunk_index}")

                chunk_index += 1
                chunk_start_time = now

            elif is_stopped:
                print("[orchestrator] recording stopped, breaking main loop")
                break

        print("[orchestrator] processing final audio segment...")
        if all_part_files:
            start_sec = chunk_index * config.CHUNK_SECONDS

            t = threading.Thread(
                target=extraction_and_transcription_thread,
                args=(
                    list(all_part_files),
                    start_sec,
                    None,
                    chunk_index,
                    transcript_store,
                ),
                daemon=True,
            )
            t.start()
            processing_threads.append(t)
        else:
            print("[orchestrator] no final audio parts to process")

        print(f"[orchestrator] waiting for {len(processing_threads)} chunk(s) to finish... (queue is managed by semaphore)")
        for t in processing_threads:
            t.join()

        print("[orchestrator] all chunks processed.")

        transcript = transcript_store.get_full_transcript()
        print(f"[orchestrator] final transcript length: {len(transcript)}")

        if not transcript.strip():
            print("[orchestrator] empty transcript, skipping GPT analysis")
            return

        analysis = analyze_with_gpt(meeting_name, meeting_topic, participants, transcript)
        folder = save_meeting_outputs(meeting_id, meeting_name, transcript, analysis)

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
                folder = save_meeting_outputs(
                    meeting_id, f"FAILED_{meeting_name}", transcript, f"ANALYSIS FAILED:\n{e}"
                )
        except Exception as save_e:
            print(f"[orchestrator] failed to save error state: {save_e}", file=sys.stderr)
    finally:
        print(f"[orchestrator] cleaning up {len(all_part_files)} part files...")
        for part_path in all_part_files:
            try:
                part_path.unlink()
            except FileNotFoundError:
                pass
        if folder:
            cleanup_meeting_folder(folder)


def start_full_pipeline_in_thread(
    audio_path: Path,
    meeting_name: str,
    meeting_topic: str,
    participants: str,
    meeting_id: str,
    user_email: str | None = None,
):
    def _run():
        try:
            full_meeting_pipeline(audio_path, meeting_name, meeting_topic, participants, meeting_id, user_email=user_email)
        finally:
            try:
                audio_path.unlink()
                print(f"[upload] cleaned up {audio_path}")
            except FileNotFoundError:
                pass

    Thread(target=_run, daemon=True).start()
