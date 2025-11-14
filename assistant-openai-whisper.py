#!/usr/bin/env python3
import os
import sys
import sounddevice as sd
import soundfile as sf
import queue
from pathlib import Path
from openai import OpenAI
import subprocess
import tempfile

# -----------------------
# CONFIG
# -----------------------
TRAITS_FILE = "damyan_traits.txt"
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 24 MB – безопасен лимит за STT (по размер на файла)
MAX_BYTES = 24 * 1024 * 1024

# Максимална дължина на аудио за един API call (gpt-4o-transcribe лимит ≈ 1400s)
MAX_SECONDS_PER_CALL = 1200  # 20 минути, под лимита


# -----------------------
# RECORD AUDIO UNTIL ENTER
# -----------------------
def record_audio_until_interrupt(filename="meeting.wav", samplerate=16000):
    print("🎙 Започвам запис. Натисни ENTER за спиране.")

    q = queue.Queue()

    def callback(indata, frames, time_, status):
        if status:
            print(f"⚠️ {status}", file=sys.stderr)
        q.put(indata.copy())

    with sf.SoundFile(filename, mode='w', samplerate=samplerate, channels=1) as f:
        with sd.InputStream(samplerate=samplerate, channels=1, callback=callback):
            input()  # чака ENTER
            print("⏹ Спирам записа.")
            while not q.empty():
                f.write(q.get())

    print(f"💾 Записано в {filename}")
    return filename


# -----------------------
# INTERNAL: FFmpeg conversion WAV -> MP3
# -----------------------
def convert_to_mp3(src_path, bitrate="48k"):
    mp3_path = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name
    cmd = [
        "ffmpeg",
        "-y",
        "-i", src_path,
        "-vn",
        "-acodec", "libmp3lame",
        "-b:a", bitrate,
        mp3_path
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return mp3_path


# -----------------------
# INTERNAL: Transcribe one MP3 chunk
# -----------------------
def _transcribe_single(mp3_path):
    with open(mp3_path, "rb") as f:
        t = client.audio.transcriptions.create(
            model="gpt-4o-transcribe",
            file=f
        )
    return t.text.strip()


# -----------------------
# TRANSCRIBE VIA OPENAI (WITH CHUNKING, NO PYDUB)
# -----------------------
def transcribe_cloud(audio_path):
    print("🟦 Транскрибирам чрез OpenAI Whisper API...")

    # --- Step 1: Convert WAV → MP3 ---
    print("🎧 Конвертирам WAV → MP3...")
    mp3_path = convert_to_mp3(audio_path)

    size = os.path.getsize(mp3_path)
    print(f"📦 MP3 size: {size / 1024 / 1024:.2f} MB")

    # --- Step 2: Проверка за продължителност ---
    try:
        probe = subprocess.check_output(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                mp3_path,
            ]
        ).decode().strip()
        duration_sec = float(probe)
    except Exception as e:
        print(f"⚠️ ffprobe проблем ({e}), приемам, че е кратко и пращам директно.")
        duration_sec = 0.0

    # Ако е под лимита по размер И по време → един call
    if size <= MAX_BYTES and (duration_sec == 0.0 or duration_sec <= MAX_SECONDS_PER_CALL):
        print("➡️ Под лимита е, изпращам директно.")
        transcript = _transcribe_single(mp3_path)
        os.remove(mp3_path)
        print("📄 Транскриптът е зареден.")
        return transcript

    # --- Step 3: Chunking via ffmpeg ---
    print("✂️ Над лимита е, режа на части с ffmpeg...")

    if duration_sec == 0.0:
        # някакъв странен случай, но да не умрем
        duration_sec = MAX_SECONDS_PER_CALL

    chunk_sec = min(10 * 60, MAX_SECONDS_PER_CALL)  # 10 мин, но под модела лимит
    parts = []

    start = 0.0
    idx = 1

    while start < duration_sec:
        end = min(start + chunk_sec, duration_sec)

        temp_mp3 = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3").name

        # Cut chunk with ffmpeg
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i", mp3_path,
                "-ss", str(start),
                "-to", str(end),
                "-c", "copy",
                temp_mp3,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        print(f"🔹 Chunk {idx}: {start:.1f}s → {end:.1f}s")

        try:
            part_text = _transcribe_single(temp_mp3)
            parts.append(part_text)
        finally:
            os.remove(temp_mp3)

        idx += 1
        start = end

    os.remove(mp3_path)
    print("📄 Транскриптът е зареден (много части).")
    return "\n\n".join(parts)


# -----------------------
# GPT ANALYSIS
# -----------------------
def analyze_with_gpt(meeting_name, meeting_topic, participants, transcript):
    print("🧠 Анализирам с GPT-5.1...")

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

    response = client.responses.create(
        model="gpt-5.1",
        input=prompt,
    )

    return response.output_text


# -----------------------
# SAVE RESULTS
# -----------------------
def save_output(meeting_name, transcript, analysis):
    safe = meeting_name.replace(" ", "_").replace(":", "_")
    folder = Path(f"meeting_{safe}")
    folder.mkdir(exist_ok=True)

    with open(folder / "transcript.txt", "w", encoding="utf-8") as f:
        f.write(transcript)

    with open(folder / "analysis.txt", "w", encoding="utf-8") as f:
        f.write(analysis)

    print(f"💾 Записах резултатите в {folder}/")


# -----------------------
# UPDATE TRAITS
# -----------------------
def update_traits(transcript, analysis):
    print("🔍 Обновявам файл с лични traits...")

    trait_prompt = f"""
You are building a long-term behavioral profile of Damyan as a collaborator.

Based ONLY on this transcript and analysis:
- Extract Damyan’s typical communication style.
- Preferred level of structure and clarity.
- Leadership and management tendencies.
- How he handles conflict, underperformance, and uncertainty.
- Any recurring patterns that future AI assistants should know when working with him.

Write 5–10 bullet points.
No repetition, no flattery, no armchair diagnosis.
Be specific and practical.

--- TRANSCRIPT ---
{transcript}

--- ANALYSIS ---
{analysis}
"""

    resp = client.responses.create(
        model="gpt-5.1",
        input=trait_prompt,
    )

    traits = resp.output_text.strip()

    with open(TRAITS_FILE, "a", encoding="utf-8") as f:
        f.write("\n\n==== NEW SESSION ====\n")
        f.write(traits)

    print("✔ Trait engine updated.")


# -----------------------
# MAIN
# -----------------------
def main():
    print("🧾 Meeting Assistant v2.0 – Cloud Whisper + GPT-5.1")

    meeting_name = input("📝 Meeting name?\n> ")
    meeting_topic = input("📝 Topic?\n> ")
    participants = input("👥 Participants count / description?\n> ")

    print("\n📌 Choose mode:")
    print("1) 🎙 Record new audio")
    print("2) 📁 Use existing WAV file")
    mode = input("> ").strip()

    if mode == "1":
        print("▶ Press ENTER to start recording...")
        input()
        audio_path = record_audio_until_interrupt()
    else:
        audio_path = input("📁 WAV file path:\n> ").strip()
        if not Path(audio_path).exists():
            print("❌ File not found!")
            return

    # TRANSCRIBE
    transcript = transcribe_cloud(audio_path)

    # ANALYZE
    analysis = analyze_with_gpt(meeting_name, meeting_topic, participants, transcript)

    # SAVE
    save_output(meeting_name, transcript, analysis)

    # TRAITS
    update_traits(transcript, analysis)


if __name__ == "__main__":
    main()