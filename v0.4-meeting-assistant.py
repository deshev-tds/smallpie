#!/usr/bin/env python3
import os
import sys
import threading
import queue
import time
import random
import subprocess
import tempfile
from pathlib import Path

import sounddevice as sd
import soundfile as sf
from openai import OpenAI

# -----------------------
# CONFIG
# -----------------------
WHISPER_CLI = "whisper-cli"
WHISPER_MODEL = "ggml-large-v3.bin"
TRAITS_FILE = "traits.txt"

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# How long a whisper chunk would be
CHUNK_SECONDS = 1 * 60  
SAMPLE_RATE = 16000


# -----------------------
# RANDOM DELAY (ANTI-429 from Sam Altman)
# -----------------------
def rand_delay(label=""):
    d = random.uniform(1.5, 5.0)
    print(f"Random delay ({label}) {d:.2f}s ...")
    time.sleep(d)


# -----------------------
# TRANSCRIBE 1 CHUNK WITH A LOCAL WHISPER
# -----------------------
def _transcribe_single_local(wav_path: str) -> str:
    """
    Пуска whisper-cli върху един .wav chunk и връща чист текст, ако даде Господ. 
    """
    # base име за output-а
    base_tmp = tempfile.NamedTemporaryFile(delete=False)
    base_path = base_tmp.name
    base_tmp.close()

    cmd = [
        WHISPER_CLI,
        "-m", WHISPER_MODEL,
        "-f", wav_path,
        "--language", "auto",
        "--no-fallback",
        "-otxt",
        "-of", base_path,
        "-t", "8",
        "--processors", "1",
    ]

    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    txt_path = base_path + ".txt"
    if not os.path.exists(txt_path):
        raise RuntimeError(f"whisper-cli не произведе TXT output за chunk {wav_path}")

    with open(txt_path, "r", encoding="utf-8") as f:
        text = f.read().strip()

    # Чистим след себе си
    try:
        os.remove(txt_path)
    except FileNotFoundError:
        pass

    try:
        os.remove(base_path)
    except FileNotFoundError:
        pass

    return text


# -----------------------
# WORKER THREAD FOR CHUNKS
# -----------------------
def chunk_worker(main_wav_path: str, task_queue: "queue.Queue", results: list):
    """
    task_queue: елементи (chunk_index, start_sec, end_sec)
    results: пълни се с (chunk_index, text)
    """
    while True:
        item = task_queue.get()
        if item is None:
            task_queue.task_done()
            break

        chunk_index, start_sec, end_sec = item
        temp_wav = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name

        # Режем chunk-а от основния файл
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", main_wav_path,
                "-ss", str(start_sec),
                "-to", str(end_sec),
                "-c", "copy",
                temp_wav,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        print(f"Transcribing chunk {chunk_index}: {start_sec:.1f}s → {end_sec:.1f}s")

        try:
            text = _transcribe_single_local(temp_wav)
            results.append((chunk_index, text))
        finally:
            try:
                os.remove(temp_wav)
            except FileNotFoundError:
                pass

        task_queue.task_done()


# -----------------------
# LIVE RECORDING + PARALLEL TRANSCRIPTION
# -----------------------
def record_and_transcribe_live(
    filename: str = "meeting.wav",
    samplerate: int = SAMPLE_RATE,
    chunk_seconds: int = CHUNK_SECONDS,
):
    print("Записвам… Натисни Ctrl+C за край.")

    samples_written = 0
    chunk_index = 0
    samples_per_chunk = samplerate * chunk_seconds

    task_queue: "queue.Queue" = queue.Queue()
    results = []

    # Chuks worker thread start
    worker = threading.Thread(
        target=chunk_worker,
        args=(filename, task_queue, results),
        daemon=True,
    )
    worker.start()

    with sf.SoundFile(filename, mode="w", samplerate=samplerate, channels=1) as f:
        with sd.InputStream(samplerate=samplerate, channels=1, dtype="float32") as stream:
            try:
                while True:
                    data, overflowed = stream.read(1024)
                    if overflowed:
                        print("Overflow в аудио буфера. Да, и това съм кепчърнал...", file=sys.stderr)

                    f.write(data)
                    samples_written += len(data)

                    # Check if we have a complete chunk
                    while samples_written >= (chunk_index + 1) * samples_per_chunk:
                        start_sec = chunk_index * chunk_seconds
                        end_sec = (chunk_index + 1) * chunk_seconds
                        print(f"Enqueue chunk {chunk_index} ({start_sec:.1f}s → {end_sec:.1f}s)")
                        task_queue.put((chunk_index, start_sec, end_sec))
                        chunk_index += 1

            except KeyboardInterrupt:
                print("Спирам записа... Halt!")

    total_duration = samples_written / samplerate if samplerate > 0 else 0.0
    print(f"Обща продължителност: {total_duration:.1f} секунди")

    # Last partial chunk, if any
    remaining_sec = total_duration - chunk_index * chunk_seconds
    if remaining_sec > 1.0:
        start_sec = chunk_index * chunk_seconds
        end_sec = total_duration
        print(f"Enqueue FINAL chunk {chunk_index} ({start_sec:.1f}s → {end_sec:.1f}s)")
        task_queue.put((chunk_index, start_sec, end_sec))
        chunk_index += 1

    # Closing the worker
    task_queue.put(None)
    task_queue.join()
    worker.join()

    # Sorting chunks by index and assembling 
    results_sorted = [txt for idx, txt in sorted(results, key=lambda x: x[0])]
    transcript = "\n\n".join(results_sorted)

    print("Транскриптът е готов (live streaming).")
    return filename, transcript


# -----------------------
# EXISTING WAV FILE TRANSCRIPTION - NO LIVE RECORDING
# -----------------------
def transcribe_existing_wav(audio_path: str, chunk_seconds: int = CHUNK_SECONDS) -> str:
    """
    Ако има вече записан WAV файл – режем го на части и ги транскрибираме последователно.
    Няма паралелен запис тук, само chunk -> whisper -> chunk -> whisper.
    """
    print("Транскрибирам съществуващ WAV чрез whisper.cpp...")

    # Използваме ffprobe, за да вземем продължителността
    probe = subprocess.check_output(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path,
        ]
    ).decode().strip()

    duration_sec = float(probe)
    print(f"Продължителност: {duration_sec:.1f} сек.")

    parts = []
    start = 0.0
    idx = 0

    while start < duration_sec:
        end = min(start + chunk_seconds, duration_sec)
        temp_wav = tempfile.NamedTemporaryFile(delete=False, suffix=".wav").name

        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", audio_path,
                "-ss", str(start),
                "-to", str(end),
                "-c", "copy",
                temp_wav,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        print(f"Chunk {idx}: {start:.1f}s → {end:.1f}s")
        text = _transcribe_single_local(temp_wav)
        parts.append(text)

        try:
            os.remove(temp_wav)
        except FileNotFoundError:
            pass

        idx += 1
        start = end

    transcript = "\n\n".join(parts)
    print("📄 Транскриптът е готов (offline file).")
    return transcript


# -----------------------
# GPT ANALYSIS
# -----------------------
def analyze_with_gpt(meeting_name, meeting_topic, participants, transcript):
    rand_delay("before GPT analysis")
    print("Анализирам срещата с GPT-5.1...")

    prompt = f"""
You are an expert meeting analyst.

Given the raw transcript of a meeting (possibly multi-lingual), do the following:

1) Reconstruct the conversation as a clean dialog with inferred speakers:
   - Use labels like "Speaker 1:", "Speaker 2:", etc.
   - Group consecutive sentences by the same speaker into paragraphs.
   - Do NOT blindly alternate speakers; infer turns from meaning.

2) Extract and list:
   - Concrete actions that any of the participants must take.
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
Participants: {participants}

--- TRANSCRIPT START ---
{transcript}
--- TRANSCRIPT END ---
"""

    resp = client.responses.create(
        model="gpt-5.1",
        input=prompt,
    )

    return resp.output_text


# -----------------------
# SAVE OUTPUT
# -----------------------
def save_output(meeting_name, transcript, analysis):
    safe = meeting_name.replace(" ", "_").replace(":", "_")
    folder = Path(f"meeting_{safe}")
    folder.mkdir(exist_ok=True)

    with open(folder / "transcript.txt", "w", encoding="utf-8") as f:
        f.write(transcript)

    with open(folder / "analysis.txt", "w", encoding="utf-8") as f:
        f.write(analysis)

    print(f"Записах резултатите в {folder}/")


# -----------------------
# UPDATE TRAITS
# -----------------------
def update_traits(transcript, analysis):
    rand_delay("before traits")
    print("Обновявам файл с лични traits...")

    prompt = f"""
You are maintaining a long-term behavioral and cognitive profile of the participants on this call.

Your goal is NOT to describe their personality in vague adjectives, 
but to extract stable, recurring *patterns* of thinking, communication, decision-making, and collaboration*
that appear in this specific meeting.

These traits must:
- be grounded ONLY in evidence from the transcript + analysis
- describe *patterns*, not one-off moments
- be phrased as practical insights future AI assistants can use to work with him effectively
- avoid psychological diagnoses or speculation
- avoid praise, value-judgments, or flattery
- avoid overgeneralizing beyond the evidence

Produce **up to 5 bullet points**, each written as:

**Pattern:**  
A short, evidence-based description of a recurring behavior or cognitive style.  
**Implications:**  
A practical guideline for AI systems collaborating with the participants on improving their professional skills.

After producing the 5 bullet points, internally generate a second, independent
version of the same 5 points using a different reasoning pathway.
Then compute a "self-consistency score" for each point:

Score 1–5:
1 = the two versions diverge strongly  
5 = the two versions describe the same pattern

Return the final bullet points with their self-consistency scores. 

For each bullet point, also add a "Stability Score" (1–5):
1 = possibly situational or one-off  
5 = highly likely to be a recurring pattern across future meetings

Use exactly this style.

--- TRANSCRIPT ---
{transcript}

--- ANALYSIS ---
{analysis}
"""

    resp = client.responses.create(
        model="gpt-5.1",
        input=prompt,
    )

    traits = resp.output_text.strip()

    with open(TRAITS_FILE, "a", encoding="utf-8") as f:
        f.write("\n\n==== NEW SESSION ====\n")
        f.write(traits)

    print("✔ Traits updated.")


# -----------------------
# MAIN
# -----------------------
def main():
    print("Meeting Assistant v0.4 – Live local whisper + Cloud GPT")

    meeting_name = input("Meeting name?\n> ")
    meeting_topic = input("Topic?\n> ")
    participants = input("Participants (count / roles)?\n> ")

    print("\nChoose mode:")
    print("1) Record new audio (live, streaming transcription)")
    print("2) Use existing WAV file (offline transcription)")
    mode = input("> ").strip()

    if mode == "1":
        audio_path, transcript = record_and_transcribe_live(
            filename="meeting.wav",
            samplerate=SAMPLE_RATE,
            chunk_seconds=CHUNK_SECONDS,
        )
    else:
        audio_path = input("WAV file path:\n> ").strip()
        if not Path(audio_path).exists():
            print("File not found!")
            return
        transcript = transcribe_existing_wav(audio_path, chunk_seconds=CHUNK_SECONDS)

    # ANALYZE
    analysis = analyze_with_gpt(meeting_name, meeting_topic, participants, transcript)

    # SAVE
    save_output(meeting_name, transcript, analysis)

    # TRAITS
    update_traits(transcript, analysis)


if __name__ == "__main__":
    main()
