import subprocess
import sys
import tempfile
from pathlib import Path

from . import config


def run_ffprobe_duration(path: Path) -> float:
    """Return duration in seconds for an audio file using ffprobe."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ]
        ).decode().strip()

        if out == "N/A":
            print(
                f"[ffprobe] duration 'N/A' for {path} (likely empty/corrupt segment)",
                file=sys.stderr,
            )
            return 0.0

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
        "-i",
        str(src_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(dst),
    ]
    print(f"[ffmpeg] {src_path} -> {dst}")
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return dst


def slice_wav_to_chunks(wav_path: Path, chunk_seconds: int) -> list[Path]:
    """
    Slice a long WAV file into smaller WAV chunks using ffmpeg.
    Returns list of chunk paths.
    """
    duration = run_ffprobe_duration(wav_path)
    if duration == 0.0:
        print(f"[slice_wav] duration is 0.0s for {wav_path}, skipping.")
        return []

    chunks = []
    start = 0.0
    idx = 1

    while start < duration:
        end = min(start + chunk_seconds, duration)
        if (end - start) < 0.1:
            break

        chunk_path = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name)

        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(wav_path),
            "-ss",
            str(start),
            "-to",
            str(end),
            "-acodec",
            "copy",
            str(chunk_path),
        ]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"[ffmpeg] chunk {idx}: {start:.1f}s -> {end:.1f}s -> {chunk_path}")
        chunks.append(chunk_path)

        idx += 1
        start = end

    return chunks


def _transcribe_single_chunk(chunk_path: Path) -> str:
    """Call whisper-cli on a single WAV chunk and return plain text transcript."""
    if not chunk_path.exists() or chunk_path.stat().st_size < 100:
        print(f"[whisper] skipping empty/invalid chunk file: {chunk_path}")
        return ""

    print(f"[whisper] waiting for semaphore to run on: {chunk_path}")
    with config.WHISPER_SEMAPHORE:
        print(f"[whisper] semaphore ACQUIRED, running on chunk: {chunk_path}")

        out_prefix = Path(tempfile.NamedTemporaryFile(delete=False).name)

        cmd = [
            config.WHISPER_CLI,
            "-m",
            config.WHISPER_MODEL,
            "-f",
            str(chunk_path),
            "-otxt",
            "-of",
            str(out_prefix),
            "-t",
            str(config.WHISPER_THREADS),
            "-l",
            "auto",
        ]

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
            out_prefix.unlink(missing_ok=True)
        except TypeError:
            if txt_candidate.exists():
                txt_candidate.unlink()
            if out_prefix.exists():
                out_prefix.unlink()

        print(f"[whisper] semaphore RELEASED for chunk: {chunk_path}")
        return text


def transcribe_wav_file(wav_file: Path) -> str:
    """Transcribes a single WAV file."""
    print(f"[pipeline] starting local transcription for {wav_file}")

    duration = run_ffprobe_duration(wav_file)
    print(f"[pipeline] wav duration ~ {duration:.1f} seconds")

    if duration == 0.0:
        print(f"[pipeline] WAV {wav_file} has 0.0 duration, aborting transcription")
        try:
            wav_file.unlink()
        except FileNotFoundError:
            pass
        return ""

    chunks = slice_wav_to_chunks(wav_file, config.CHUNK_SECONDS)
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
        wav_file.unlink()
    except FileNotFoundError:
        pass

    transcript = "\n\n".join(p for p in parts if p.strip())
    print("[pipeline] transcription complete, length:", len(transcript))
    return transcript
