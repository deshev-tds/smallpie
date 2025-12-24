import "/src/styles/base.css";

// API CONFIG
// IMPORTANT: set API_BOOTSTRAP_SECRET to the same value as SMALLPIE_BOOTSTRAP_SECRET on the backend.
// Pulled from build-time env (Vite) or a runtime global to avoid hardcoding secrets.
const API_HTTP_BASE = import.meta.env.VITE_API_HTTP_BASE || "https://api.smallpie.fun";
const API_WS_URL = import.meta.env.VITE_API_WS_URL || "wss://api.smallpie.fun/ws";
const API_BOOTSTRAP_SECRET =
  import.meta.env.VITE_API_BOOTSTRAP_SECRET || (typeof window !== "undefined" ? window.__SMALLPIE_BOOTSTRAP_SECRET : "");

async function fetchSessionToken(scope = "ws") {
  const formData = new FormData();
  formData.append("scope", scope);

  const res = await fetch(`${API_HTTP_BASE}/api/token`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${API_BOOTSTRAP_SECRET}`
    },
    body: formData
  });

  if (!res.ok) {
    throw new Error(`Token request failed (${res.status})`);
  }

  const data = await res.json();
  if (!data.token) {
    throw new Error("Token response missing token");
  }
  return data;
}

// STATIC ELEMENTS
const recButton = document.getElementById("start-recording");
const recordLabel = document.getElementById("record-label");
const recordHelper = document.getElementById("record-helper");
const recordTimerEl = document.getElementById("record-timer");
const recordErrorEl = document.getElementById("record-error");
// VISUALIZER ELEMENT
const visualizerCanvas = document.getElementById("audio-visualizer");

const btnUseFile = document.getElementById("use-file");

const backdrop = document.getElementById("backdrop");
const flowContainer = document.getElementById("flow-container");

// Templates
const tmplForm = document.getElementById("tmpl-form-section");
const tmplUpload = document.getElementById("tmpl-file-upload-section");
const tmplStatus = document.getElementById("tmpl-status");

// STATE
let mediaRecorder = null;
let mediaStream = null; // <-- keep track of the underlying MediaStream
let ws = null;
let recordingState = "idle"; // idle | recording | finishing | finished | error
let recordingStartTime = null;
let recordingTimerInterval = null;
let currentMode = "idle"; // idle | record | upload
let droppedFile = null;

// VISUALIZER STATE
let audioContext = null;
let analyser = null;
let audioSource = null;
let gainNode = null;
let visualizerFrameId = null;
let visualizerStream = null; // <-- ADDED: Track the cloned stream to release it later

// ------------------------------------------------
// FLOW UTILS
// ------------------------------------------------

function clearFlow() {
  while (flowContainer.firstChild) {
    flowContainer.removeChild(flowContainer.firstChild);
  }
}

function showScreen(template, options = {}) {
  const { showBackdropFlag = true } = options;

  clearFlow();

  if (template) {
    const node = template.content.cloneNode(true);
    flowContainer.appendChild(node);

    if (showBackdropFlag) {
      showBackdrop();
    } else {
      // status / upload без backdrop
      backdrop.classList.add("hidden");
    }

    wireDynamicHandlers();
  } else {
    hideBackdrop();
  }
}

function showBackdrop() {
  backdrop.classList.remove("hidden");
}

function hideBackdrop() {
  backdrop.classList.add("hidden");
  clearFlow();
  if (currentMode === "record" && recordingState === "idle") {
    setRecordingState("idle");
  }
}

// Clicking outside closes active container (form / upload / status)
backdrop.onclick = () => {
  hideBackdrop();
};

// ------------------------------------------------
// RECORDING STATE UI
// ------------------------------------------------

function setRecordingState(state) {
  recordingState = state;

  // reset error by default
  recordErrorEl.classList.add("hidden");

  switch (state) {
    case "idle":
      recButton.classList.remove("animate-pulse", "opacity-70", "cursor-not-allowed");
      recButton.disabled = false;
      recButton.textContent = "REC";
      recordLabel.textContent = "REC";
      recordHelper.textContent = "Tap REC to start listening.";
      recordTimerEl.classList.add("hidden");
      visualizerCanvas.classList.add("hidden");
      stopRecordingTimer();
      break;

    case "recording":
      recButton.classList.add("animate-pulse");
      recButton.textContent = "STOP";
      recButton.classList.remove("opacity-70", "cursor-not-allowed");
      recButton.disabled = false;
      recordLabel.textContent = "STOP";
      recordHelper.textContent = "Recording… tap STOP when you’re done.";
      recordTimerEl.classList.remove("hidden");
      visualizerCanvas.classList.remove("hidden");
      startRecordingTimer();
      break;

    case "finishing":
      recButton.classList.remove("animate-pulse");
      recButton.classList.add("opacity-70", "cursor-not-allowed");
      recButton.disabled = true;
      recordHelper.textContent = "Sending the last audio chunks to smallpie.";
      visualizerCanvas.classList.add("hidden");
      break;

    case "finished":
      recButton.classList.remove("animate-pulse", "opacity-70", "cursor-not-allowed");
      recButton.disabled = false;
      recButton.textContent = "REC";
      recordLabel.textContent = "REC";
      recordHelper.textContent = "Recording finished. Tap REC to start a new one.";
      recordTimerEl.classList.add("hidden");
      visualizerCanvas.classList.add("hidden");
      stopRecordingTimer();
      break;

    case "error":
      recButton.classList.remove("animate-pulse", "opacity-70", "cursor-not-allowed");
      recButton.disabled = false;
      recButton.textContent = "REC";
      recordLabel.textContent = "REC";
      recordHelper.textContent = "Something went wrong. You can try again.";
      recordErrorEl.classList.remove("hidden");
      visualizerCanvas.classList.add("hidden");
      stopRecordingTimer();
      break;
  }
}

function fadeOutStatusCardAndFinish() {
  const card = document.getElementById("status-card");
  if (!card) {
    setRecordingState("finished");
    return;
  }

  setTimeout(() => {
    card.style.opacity = "0";
    setTimeout(() => {
      card.remove();
      setRecordingState("finished");
    }, 600);
  }, 3000);
}

function startRecordingTimer() {
  recordingStartTime = Date.now();
  if (recordingTimerInterval) clearInterval(recordingTimerInterval);

  recordingTimerInterval = setInterval(() => {
    const elapsedMs = Date.now() - recordingStartTime;
    const totalSeconds = Math.floor(elapsedMs / 1000);
    const minutes = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
    const seconds = String(totalSeconds % 60).padStart(2, "0");
    recordTimerEl.textContent = `${minutes}:${seconds} • Recording…`;
  }, 1000);
}

function stopRecordingTimer() {
  if (recordingTimerInterval) {
    clearInterval(recordingTimerInterval);
    recordingTimerInterval = null;
  }
}

// ------------------------------------------------
// AUDIO VISUALIZER LOGIC
// ------------------------------------------------

// Helper to ensure Context exists and is running (must call on user gesture)
function ensureAudioContext() {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioContext.state === "suspended") {
    // This returns a promise, but we fire it eagerly to capture the gesture
    audioContext.resume().catch(e => console.error("AudioContext resume failed", e));
  }
}

async function startVisualizer(stream) {
  ensureAudioContext();
  
  // === FIX 1: Clone the stream ===
  // This ensures the Visualizer and MediaRecorder don't fight over the same track state.
  visualizerStream = stream.clone(); // <-- Store in global var to close later

  // 2. Build Graph: Source -> Gain -> Analyser
  audioSource = audioContext.createMediaStreamSource(visualizerStream);
  
  // Boost gain for visibility
  gainNode = audioContext.createGain();
  gainNode.gain.value = 7.0; // Increased gain (10.0 -> 12.0)
  
  analyser = audioContext.createAnalyser();
  analyser.fftSize = 256; // 128 bins
  analyser.smoothingTimeConstant = 0.5;
  analyser.minDecibels = -90;
  analyser.maxDecibels = -10;

  audioSource.connect(gainNode);
  gainNode.connect(analyser);

  const bufferLength = analyser.frequencyBinCount; // 128
  const dataArray = new Uint8Array(bufferLength);
  const ctx = visualizerCanvas.getContext("2d");

  // === FIX 2: Dense Binning (Summation) ===
  // Instead of picking specific indices, we calculate average energy
  // across 5 distinct bands covering the vocal range (0Hz to ~4kHz).
  const bars = 5;
  const binsPerBar = 5; // 5 * 5 = 25 bins covered (~4.6kHz total width)

  const draw = () => {
    visualizerFrameId = requestAnimationFrame(draw);

    analyser.getByteFrequencyData(dataArray);
    ctx.clearRect(0, 0, visualizerCanvas.width, visualizerCanvas.height);
    ctx.fillStyle = "#e11d48"; // Rose color

    const gap = 3;
    const totalWidth = visualizerCanvas.width;
    const barWidth = (totalWidth / bars) - gap;
    
    for (let i = 0; i < bars; i++) {
      let sum = 0;
      // Sum up energy in this band
      for (let j = 0; j < binsPerBar; j++) {
        // Offset by 1 to skip DC offset
        const binIndex = 1 + (i * binsPerBar) + j; 
        if (binIndex < bufferLength) {
          sum += dataArray[binIndex];
        }
      }
      // Average
      const avg = sum / binsPerBar;

      // Visual scaling
      let percent = avg / 255;
      if (percent > 0.02) {
         // Non-linear boost for visibility + increased multiplier (1.2 -> 1.6)
         percent = Math.sqrt(percent) * 1.6;
      }
      
      // Clamping
      if (percent > 1) percent = 1;
      if (percent < 0.1) percent = 0.1; // Minimum pill height

      const barHeight = visualizerCanvas.height * percent;
      const x = i * (barWidth + gap) + gap/2;
      const y = (visualizerCanvas.height - barHeight) / 2;

      ctx.beginPath();
      ctx.roundRect(x, y, barWidth, barHeight, 4);
      ctx.fill();
    }
  };

  draw();
}

function stopVisualizer() {
  if (visualizerFrameId) {
    cancelAnimationFrame(visualizerFrameId);
    visualizerFrameId = null;
  }
  
  const ctx = visualizerCanvas.getContext("2d");
  ctx.clearRect(0, 0, visualizerCanvas.width, visualizerCanvas.height);

  if (audioSource) {
    audioSource.disconnect();
    audioSource = null;
  }
  if (gainNode) {
    gainNode.disconnect();
    gainNode = null;
  }
  if (analyser) {
    analyser.disconnect();
    analyser = null;
  }

  // === CRITICAL FIX: Stop the cloned tracks ===
  if (visualizerStream) {
    visualizerStream.getTracks().forEach(track => track.stop());
    visualizerStream = null;
  }

  if (audioContext && audioContext.state !== 'closed') {
     audioContext.close();
     audioContext = null;
  }
}

// ------------------------------------------------
// DYNAMIC HANDLERS (FORM / UPLOAD / STATUS)
// ------------------------------------------------

function wireDynamicHandlers() {
  // Form
  const form = document.getElementById("meeting-form");
  const cancelBtn = document.getElementById("meeting-cancel");

  if (cancelBtn) {
    cancelBtn.onclick = () => {
      hideBackdrop();
      currentMode = "idle";
      setRecordingState("idle");
    };
  }

  if (form) {
    form.onsubmit = async (e) => {
      e.preventDefault();
      const name = document.getElementById("meeting-name").value.trim();
      const topic = document.getElementById("meeting-topic").value.trim();
      const participants = document.getElementById("meeting-participants").value.trim();
      const email = document.getElementById("meeting-email").value.trim();

      if (!name || !topic || !participants || !email) {
        alert("Please fill in all fields.");
        return;
      }

      // Synchronous Resume to capture user gesture
      ensureAudioContext();

      showScreen(tmplStatus, { showBackdropFlag: false });
      const statusText = document.getElementById("status-text");
      const statusSubtext = document.getElementById("status-subtext");

      if (statusText) statusText.innerText = "Connecting to audio service…";
      if (statusSubtext)
        statusSubtext.innerText = "We’re preparing to record your meeting in real time.";

      try {
        await startRecordingAndStreaming({
          meeting_name: name,
          meeting_topic: topic,
          participants,
          user_email: email,
        });
      } catch (err) {
        console.error(err);
        if (statusText) statusText.innerText = "Error starting recording.";
        setRecordingState("error");
      }
    };
  }

  // Upload panel
  const uploadBtn = document.getElementById("upload-file-btn");
  const audioInput = document.getElementById("audio-file");
  const dropzone = document.getElementById("upload-dropzone");
  const uploadCancelBtn = document.getElementById("upload-cancel");

  if (uploadCancelBtn) {
    uploadCancelBtn.onclick = () => {
      hideBackdrop();
      currentMode = "idle";
    };
  }

  if (dropzone) {
    dropzone.addEventListener("click", () => {
      audioInput?.click();
    });

    dropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add("ring-2", "ring-gold", "bg-bgSubtle", "dark:bg-darkBgSoft");
    });

    ["dragleave", "drop"].forEach((ev) => {
      dropzone.addEventListener(ev, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.remove("ring-2", "ring-gold", "bg-bgSubtle", "dark:bg-darkBgSoft");
      });
    });

    dropzone.addEventListener("drop", (e) => {
      const files = e.dataTransfer?.files;
      if (files && files.length > 0) {
        droppedFile = files[0];
        if (audioInput) {
          audioInput.files = files;
        }
      }
    });
  }

  if (audioInput) {
    audioInput.onchange = (e) => {
      const files = e.target.files;
      if (files && files.length > 0) {
        droppedFile = files[0];
      }
    };
  }

  if (uploadBtn) {
    uploadBtn.onclick = () => {
      const file = droppedFile || audioInput?.files?.[0];
      if (!file) {
        alert("Please select or drop a file before starting.");
        return;
      }

      showScreen(tmplStatus, { showBackdropFlag: false });
      const statusText = document.getElementById("status-text");
      const statusSubtext = document.getElementById("status-subtext");

      if (statusText) statusText.innerText = "Uploading file…";
      if (statusSubtext) statusSubtext.innerText = "We’re analysing your audio and generating notes.";

      // TODO: hook real upload logic here (fetch token via fetchSessionToken('upload') and call /api/meetings/upload)
      // For now just simulate:
      setTimeout(() => {
        if (statusText) statusText.innerText = "Processing finished.";
        if (statusSubtext) statusSubtext.innerText = "Your transcript and notes are ready.";
      }, 3000);
    };
  }
}

// ------------------------------------------------
// UI HANDLERS FOR MAIN CARDS
// ------------------------------------------------

// REC BUTTON: idle → open form; recording → stop
recButton.onclick = () => {
  if (recordingState === "idle" || recordingState === "finished" || recordingState === "error") {
    currentMode = "record";
    showScreen(tmplForm);
  } else if (recordingState === "recording") {
    // User tapped STOP
    setRecordingState("finishing");

    const statusText = document.getElementById("status-text");
    const statusSubtext = document.getElementById("status-subtext");
    if (statusText) statusText.innerText = "Audio received. Processing…";
    if (statusSubtext)
      statusSubtext.innerText =
        "You can stay on this page. Your notes are being generated in the background.";

    fadeOutStatusCardAndFinish();
    stopRecording();
  }
};

// UPLOAD CARD
btnUseFile.onclick = () => {
  currentMode = "upload";
  showScreen(tmplUpload, { showBackdropFlag: false });
};

// ------------------------------------------------
// RECORDING + WEBSOCKET LOGIC
// ------------------------------------------------

async function startRecordingAndStreaming(metadata) {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("getUserMedia not supported in this browser.");
  }

  // Reset state
  setRecordingState("idle");
  recordErrorEl.classList.add("hidden");
  droppedFile = null; // irrelevant here

  const tokenResponse = await fetchSessionToken("ws");

  // Attach token as query param
  const wsUrl = `${API_WS_URL}?token=${encodeURIComponent(tokenResponse.token)}`;
  ws = new WebSocket(wsUrl);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    console.log("WS connected");
    ws.send(
      JSON.stringify({
        type: "metadata",
        session_id: tokenResponse.session_id,
        ...metadata,
      })
    );

    const statusText = document.getElementById("status-text");
    const statusSubtext = document.getElementById("status-subtext");

    if (statusText) statusText.innerText = "Recording live…";
    if (statusSubtext) statusSubtext.innerText = "Speak naturally. We’re capturing everything.";

    setRecordingState("recording");
  };

  ws.onerror = (err) => {
    console.error("WebSocket error:", err);
    const statusText = document.getElementById("status-text");
    const statusSubtext = document.getElementById("status-subtext");
    if (statusText) statusText.innerText = "Connection error.";
    if (statusSubtext) statusSubtext.innerText = "Please check your connection and try again.";
    setRecordingState("error");
  };

  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);

    if (data.type === "final_transcript") {
      const statusText = document.getElementById("status-text");
      const statusSubtext = document.getElementById("status-subtext");
      if (statusText) statusText.innerText = "Processing finished.";
      if (statusSubtext) statusSubtext.innerText = "Your transcript and notes are ready.";
      fadeOutStatusCardAndFinish();
      stopRecording();
    }

    if (data.type === "error") {
      const statusText = document.getElementById("status-text");
      const statusSubtext = document.getElementById("status-subtext");
      if (statusText) statusText.innerText = "Server error.";
      if (statusSubtext)
        statusSubtext.innerText = data.message || "Something went wrong on our side.";
      setRecordingState("error");
    }
  };

  ws.onclose = () => {
    console.log("WS closed");
    if (recordingState === "recording" || recordingState === "finishing") {
      // If closed unexpectedly during recording, show error
      if (recordingState !== "finishing") {
        const statusText = document.getElementById("status-text");
        const statusSubtext = document.getElementById("status-subtext");
        if (statusText) statusText.innerText = "Connection closed.";
        if (statusSubtext) statusSubtext.innerText = "The connection ended unexpectedly.";
        setRecordingState("error");
      }
    }
  };

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaStream = stream; 

    // === START VISUALIZER (Async) ===
    await startVisualizer(stream);

    mediaRecorder = new MediaRecorder(stream, {
      mimeType: "audio/webm;codecs=opus",
    });

    mediaRecorder.ondataavailable = (e) => {
      if (ws?.readyState === WebSocket.OPEN && e.data.size > 0) {
        e.data.arrayBuffer().then((buf) => {
          ws.send(buf);
        });
      }
    };

    mediaRecorder.onstop = () => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "end" }));
      }
    };

    // 5000ms (5s) ensures each blob is a valid, self-contained file
    mediaRecorder.start(5000);
  } catch (err) {
    console.error("getUserMedia error:", err);
    const statusText = document.getElementById("status-text");
    const statusSubtext = document.getElementById("status-subtext");
    if (statusText) statusText.innerText = "Microphone error.";
    if (statusSubtext)
      statusSubtext.innerText =
        "We can’t access your microphone. Check permissions and try again.";
    recordErrorEl.classList.remove("hidden");
    setRecordingState("error");
  }
}

function stopRecording() {
  // === STOP VISUALIZER ===
  stopVisualizer();

  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }

  // Explicitly release the microphone
  if (mediaStream) {
    mediaStream.getTracks().forEach((track) => track.stop());
    mediaStream = null;
  }

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "end" }));
  }
}
