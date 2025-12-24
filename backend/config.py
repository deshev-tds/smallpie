#!/usr/bin/env python3
"""
Centralized configuration and shared singletons for the backend.
Imports remain side-effectful (env reads + prints) to preserve legacy behavior.
"""
import os
import random
import threading
from pathlib import Path
import secrets

from openai import OpenAI

# Local whisper.cpp CLI + model
WHISPER_CLI = "/root/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL = "/root/whisper.cpp/models/ggml-large-v3-q5_0.bin"

# Chunking / threading
CHUNK_SECONDS = 60
WHISPER_THREADS = 6
WHISPER_SEMAPHORE = threading.Semaphore(1)
print(f"[config] Whisper concurrency limit set to 1 (using {WHISPER_THREADS} threads per job)")

# Auth / token signing
SIGNING_KEY = os.getenv("SMALLPIE_SIGNING_KEY", "").strip() or secrets.token_hex(32)
BOOTSTRAP_SECRET = os.getenv("SMALLPIE_BOOTSTRAP_SECRET", "").strip()
TOKEN_TTL_SECONDS = int(os.getenv("SMALLPIE_TOKEN_TTL_SECONDS", "600"))
TOKEN_ISSUE_LIMIT = int(os.getenv("SMALLPIE_TOKEN_ISSUE_LIMIT", "30"))  # per window
TOKEN_ISSUE_WINDOW_SECONDS = int(os.getenv("SMALLPIE_TOKEN_ISSUE_WINDOW_SECONDS", "300"))
TOKEN_VERIFY_LIMIT = int(os.getenv("SMALLPIE_TOKEN_VERIFY_LIMIT", "120"))  # per window
TOKEN_VERIFY_WINDOW_SECONDS = int(os.getenv("SMALLPIE_TOKEN_VERIFY_WINDOW_SECONDS", "300"))

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
    "SIGNING_KEY",
    "BOOTSTRAP_SECRET",
    "TOKEN_TTL_SECONDS",
    "TOKEN_ISSUE_LIMIT",
    "TOKEN_ISSUE_WINDOW_SECONDS",
    "TOKEN_VERIFY_LIMIT",
    "TOKEN_VERIFY_WINDOW_SECONDS",
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
