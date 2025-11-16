# smallpie
Meeting intelligence, but cute :3

Version 0.5 (frontend + local Python pipeline)

smallpie is a tiny, browser-based (explicity mobile-friendly), tasty, portable prototype that turns meetings (ones that it can listen to from your phone, 
computer, raspberry, your grandma's laptop...) into structured transcripts, analysis, and actionable items.

The system currently has two main components:

- Frontend (Vite + Tailwind, mobile-friendly, runs anywhere)
- Python pipeline (local Whisper.cpp + GPT-5.1 analysis)

The backend with WebSockets arrives in version 0.7.


## Нож 16, 2025: v0.6.0 - Streaming Transcription Architecture

### Added
- Introduced a new **live transcription orchestrator**, enabling
  near-real-time processing of audio during the recording session.
- Implemented **parallel chunk-based transcription**:
  audio is segmented into fixed-duration slices (5s chinks from frontend, concatenated into 60s on backend) 
  while streaming. Each slice is immediately handed off to whisper.cpp in a background thread for 
  parallel processing while recording is still running on the frontend. 
- Added a thread-safe transcript aggregator to merge partial transcriptions
  into the final meeting transcript.
- Integrated final-segment duration detection to prevent empty or corrupt
  last-chunk artifacts (`ffprobe: duration 'N/A'` errors avoided).

### Improved
- WebSocket recording endpoint now flushes raw audio safely before
  signaling the orchestrator to finalize.
- Enhanced error handling around ffmpeg/ffprobe to avoid transient corruption states.
- Simplified STOP message detection and improved metadata parsing.
- Basic auth restored for REST API endpoints while remaining disabled for `/ws`.
- Implemented 

### Fixed
- Eliminated race conditions when processing the last audio segment.
- Prevented whisper.cpp from receiving zero-length WAV files.
- Resolved issues where nginx basic auth could break WebSocket upgrade requests.

## WebSocket Access Token Added

The `/ws` endpoint is intentionally **not protected by nginx basic auth**,
because it would break the WebSocket upgrade handshake.

Instead, the backend uses a simple **internal WebSocket token** to prevent
unauthorized clients from opening a streaming session.

### How it works

The frontend must provide a query parameter:

    wss://api.smallpie.fun/ws?token="$API_TOKEN"

The backend validates this token via:

- an environment variable (`SMALLPIE_WS_TOKEN`), or  
- a local config value (depending on deployment)

If the token does not match, the server immediately closes the connection with 403. 

------------------------------------------------------------

## Limitations in v0.5

- No WebSocket backend
- No HTTPS (yet)
- Frontend audio recording is NOT connected to backend
- No ICS generation
- No email sending
- No multi-user support

All of this lands in **v0.7+**.

------------------------------------------------------------

## Install & Run (Ubuntu 22+)

Prereqs:
sudo apt update && sudo apt install -y nodejs npm python3 python3-pip ffmpeg

------------------------------------------------------------

Frontend:

cd smallpie/frontend
npm install
npm run dev -- --host 0.0.0.0

Open:
http://YOUR_SERVER_IP:5173/

------------------------------------------------------------

## Python Pipeline (local mode)

Dependencies:
pip install sounddevice soundfile openai ffmpeg-python

Run:
python3 assistant-openai-whisper.py

Requires:
- whisper.cpp compiled (make)
- whisper-cli in PATH
- ffmpeg installed
- OpenAI API key

------------------------------------------------------------

## Roadmap

v0.6
- WebSocket ingestion design
- Nginx reverse proxy skeleton
- HTTPS certificate automation
- First connection between frontend & backend

v0.7
- Real-time audio streaming
- Parallel transcription queue
- ICS file creation
- Gmail transcript sending
- Multi-user support

------------------------------------------------------------

## Vision

smallpie aims to be:
- Friendly  
- Minimal  
- Fast  
- Useful  
- Private  

------------------------------------------------------------

## Legal and Ethical Considerations

smallpie processes sensitive meeting data, including voice recordings, transcripts, participant names, and meeting context. To ensure responsible use and compliance with applicable laws, 
please review the following considerations carefully. 

### 1. User Responsibility for Consent
Recording conversations is regulated differently around the world.  
**You, the user, are solely responsible for informing all meeting participants** that the session will be recorded, transcribed, and analyzed.  
smallpie provides recording and analysis tools but does not verify or enforce participant consent.

### 2. Handling of Personal Data
smallpie may process the following types of data:
- Audio recordings (processed locally and deleted afterward)
- Transcripts
- Participant names and meeting metadata
- AI-generated summaries and insights

All data is processed **temporarily**.  
smallpie does **not** store meeting data long-term, does **not** build user histories, and does **not** retain audio or transcripts after delivering results to the user.

### 3. Use of External AI Services
Analyses are performed using external AI models (e.g., OpenAI).  
This means:
- Transcripts and meeting metadata may be sent to an external provider.
- External providers apply their own Terms of Service, privacy protections, retention policies, and limitations.
- smallpie does not control or guarantee the behavior, accuracy, or reliability of external AI outputs.

### 4. Accuracy and Limitations of AI Analysis
AI-generated content may and will include many inaccuracies. Some examples of such are:
- Misidentification of speakers
- Incorrect diarization
- Misinterpretation of statements
- Incomplete or biased summaries

AI outputs should be treated as **assistive insights**, not authoritative truth.

### 5. Professional Profiles and Behavioral Insights
If enabled, smallpie may generate communication-style or professional-development profiles for meeting participants. These profiles:
- Are generated automatically by AI  
- Are intended **solely for personal growth and meeting improvement**  
- **Must not** be used in any way that resembles or could reasonably be interpreted as psychological evaluation, personality assessment, clinical judgment, HR analysis, employee monitoring, or any related practice  
- Must not be used to support, justify, influence, or contribute to employment-related decisions - including, **but not limited to**, hiring, firing, promotion, demotion,
  compensation, disciplinary measures, performance scoring, role assignment, or any other action that affects a person’s professional status, opportunities, or reputation  

These restrictions apply **regardless of context, intent, or interpretation**.

### 6. Security and Transmission
Audio is processed locally on the user's device before transmission.
Audio is processed locally on the smallpie's insfrastructure before transcription. 
Textual data sent to external AI providers is transmitted over encrypted channels.  
Users are solely responsible for ensuring that no confidential, sensitive, proprietary, or legally regulated information is shared or processed through smallpie. All use must comply with applicable laws, 
regulations, contractual obligations, and ethical standards. 
smallpie does not validate the nature of submitted content and assumes no liability for how users handle protected information.

### 7. Disclaimer
smallpie is provided **“AS IS”**, without warranties of any kind.  
By using smallpie, you accept full responsibility for:
- How you record, transmit, and process meeting data  
- Ensuring full legal compliance in your jurisdiction  
- Following both the **letter** and the **spirit** of all applicable laws, rules, regulations, contractual obligations, and ethical standards  
- Avoiding the submission of confidential, sensitive, or regulated information  
- Interpreting, validating, and acting on AI-generated results with the situationally appropriate judgment  

smallpie does not guarantee accuracy, completeness, legal validity, or suitability of any output.  
All use is at the user’s own risk.
