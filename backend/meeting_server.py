#!/usr/bin/env python3
"""
Facade entrypoint that preserves the original import surface.
The real implementation now lives in smaller modules.
"""
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    # Allow running as a script from repo root.
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from smallpie.backend import config  # type: ignore
    from smallpie.backend.api import app  # type: ignore
    from smallpie.backend.pipeline import full_meeting_pipeline  # type: ignore
else:
    from . import config
    from .api import app
    from .pipeline import full_meeting_pipeline


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

    full_meeting_pipeline(audio_path, meeting_name, meeting_topic, participants, None, user_email=None)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(
        (".wav", ".mp3", ".webm", ".m4a", ".aac", ".ogg")
    ):
        cli_main()
    else:
        print("This module is intended to be run with uvicorn as an ASGI app, e.g.:")
        print("  uvicorn smallpie.backend.meeting_server:app --host 0.0.0.0 --port 8000")
