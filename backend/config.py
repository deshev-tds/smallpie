#!/usr/bin/env python3
"""
Centralized configuration and shared singletons for the backend.
Imports remain side-effectful (env reads + prints) to preserve legacy behavior.
"""
import os
import random
import threading
from pathlib import Path

from openai import OpenAI

# Local whisper.cpp CLI + model
WHISPER_CLI = "/root/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL = "/root/whisper.cpp/models/ggml-large-v3-q5_0.bin"

# Chunking / threading
CHUNK_SECONDS = 60
WHISPER_THREADS = 6
WHISPER_SEMAPHORE = threading.Semaphore(1)
print(f"[config] Whisper concurrency limit set to 1 (using {WHISPER_THREADS} threads per job)")

# Storage layout
BASE_DIR = Path("/root/smallpie-data").resolve()
AUDIO_DIR = BASE_DIR / "audio"
MEETINGS_DIR = BASE_DIR / "meetings"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
MEETINGS_DIR.mkdir(parents=True, exist_ok=True)

# OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Email / SMTP
SMTP_HOST = os.getenv("SMALLPIE_SMTP_HOST")
SMTP_PORT = int(os.getenv("SMALLPIE_SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMALLPIE_SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMALLPIE_SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMALLPIE_SMTP_FROM") or SMTP_USERNAME or "no-reply@smallpie.local"

EMAIL_ENABLED = bool(SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD)
if not EMAIL_ENABLED:
    print("[email] SMTP not fully configured; email sending is disabled")

# Simple bearer token auth
ACCESS_TOKEN = os.getenv("SMALLPIE_ACCESS_TOKEN", "").strip()
AUTH_ENABLED = bool(ACCESS_TOKEN)

if AUTH_ENABLED:
    print("[auth] Bearer token auth ENABLED for HTTP + WS")
else:
    print("[auth] Bearer token auth DISABLED (SMALLPIE_ACCESS_TOKEN not set)")

# CORS
ALLOW_ORIGINS = ["*"]  # preserved for MVP

__all__ = [
    "WHISPER_CLI",
    "WHISPER_MODEL",
    "CHUNK_SECONDS",
    "WHISPER_THREADS",
    "WHISPER_SEMAPHORE",
    "BASE_DIR",
    "AUDIO_DIR",
    "MEETINGS_DIR",
    "client",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "EMAIL_ENABLED",
    "ACCESS_TOKEN",
    "AUTH_ENABLED",
    "ALLOW_ORIGINS",
]
