from pathlib import Path

from . import config


def save_meeting_outputs(meeting_id: str, meeting_name: str, transcript: str, analysis: str) -> Path:
    """
    Save transcript + analysis under MEETINGS_DIR/meeting_<id>/.
    Returns folder path.
    """
    safe_name = meeting_name.replace(" ", "_").replace(":", "_")
    folder = config.MEETINGS_DIR / f"meeting_{meeting_id}_{safe_name}"
    folder.mkdir(parents=True, exist_ok=True)

    (folder / "transcript.txt").write_text(transcript, encoding="utf-8")
    (folder / "analysis.txt").write_text(analysis, encoding="utf-8")

    print("[save] outputs written to", folder)
    return folder
