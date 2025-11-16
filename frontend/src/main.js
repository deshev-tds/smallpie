import "/src/styles/base.css";

// API CONFIG
// IMPORTANT: set API_TOKEN to the same value as SMALLPIE_ACCESS_TOKEN on the backend.
const API_WS_URL = "wss://api.smallpie.fun/ws";
const API_TOKEN = "Iuhfjkdskeqrrgyubhoijkbcvt7gyiuhkjbwr";

// STATIC ELEMENTS
const recButton = document.getElementById("start-recording");
const recordLabel = document.getElementById("record-label");
const recordHelper = document.getElementById("record-helper");
const recordTimerEl = document.getElementById("record-timer");
const recordErrorEl = document.getElementById("record-error");

const btnUseFile = document.getElementById("use-file");

const backdrop = document.getElementById("backdrop");
const flowContainer = document.getElementById("flow-container");

// Templates
const tmplForm = document.getElementById("tmpl-form-section");
const tmplUpload = document.getElementById("tmpl-file-upload-section");
const tmplStatus = document.getElementById("tmpl-status");

// STATE
let mediaRecorder = null;
let ws = null;
let recordingState = "idle"; // idle | recording | finishing | finished | error
let recordingStartTime = null;
let recordingTimerInterval = null;
let currentMode = "idle"; // idle | record | upload
let droppedFile = null;

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
      // NEW: статус картата да не идва с backdrop, не замъгляваме страницата
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
      recordLabel.textContent = "REC";
      recordHelper.textContent = "Tap REC to start listening.";
      recordTimerEl.classList.add("hidden");
      stopRecordingTimer();
      break;

    case "recording":
      recButton.classList.add("animate-pulse");
      recButton.disabled = false;
      recordLabel.textContent = "STOP";
      recordHelper.textContent = "Recording… tap STOP when you’re done.";
      recordTimerEl.classList.remove("hidden");
      startRecordingTimer();
      break;

    case "finishing":
      recButton.classList.remove("animate-pulse");
      recButton.classList.add("opacity-70", "cursor-not-allowed");
      recButton.disabled = true;
      recordLabel.textContent = "Finishing…";
      recordHelper.textContent = "Sending the last audio chunks to smallpie.";
      break;

    case "finished":
      recButton.classList.remove("animate-pulse", "opacity-70", "cursor-not-allowed");
      recButton.disabled = false;
      recordLabel.textContent = "REC";
      recordHelper.textContent = "Recording finished. Tap REC to start a new one.";
      stopRecordingTimer();
      break;

    case "error":
      recButton.classList.remove("animate-pulse");
      recButton.disabled = false;
      recordLabel.textContent = "REC";
      recordHelper.textContent = "Something went wrong. You can try again.";
      recordErrorEl.classList.remove("hidden");
      stopRecordingTimer();
      break;
  }
}

function fadeOutStatusCardAndFinish() {
  const card = document.getElementById("status-card");
  if (!card) {
    // Fallback: just reset UI state
    setRecordingState("finished");
    return;
  }

  // Държим REC disabled (state = "finishing"), докато картата fade-не.
  setTimeout(() => {
    card.style.opacity = "0";
    setTimeout(() => {
      card.remove();
      setRecordingState("finished");
    }, 600); // match CSS transition duration
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
    recordTimerEl.textContent = `${minutes}:${seconds}`;
  }, 1000);
}

function stopRecordingTimer() {
  if (recordingTimerInterval) {
    clearInterval(recordingTimerInterval);
    recordingTimerInterval = null;
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

      if (!name || !topic || !participants) {
        alert("Please fill in all fields.");
        return;
      }

      // move to status screen & start recording
      showScreen(tmplStatus, { showBackdropFlag: false });
      const statusText = document.getElementById("status-text");
      const statusSubtext = document.getElementById("status-subtext");

      if (statusText) statusText.innerText = "Connecting to audio service…";
      if (statusSubtext)
        statusSubtext.innerText = "We’re preparing to record your meeting in real time.";

      try {
        await startRecordingAndStreaming({ name, topic, participants });
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

      // TODO: hook real upload logic here (with Authorization: Bearer API_TOKEN)
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

  // Attach token as query param
  const wsUrl = `${API_WS_URL}?token=${encodeURIComponent(API_TOKEN)}`;
  ws = new WebSocket(wsUrl);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    console.log("WS connected");
    ws.send(JSON.stringify({ type: "metadata", ...metadata }));

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

    mediaRecorder.start(300);
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
  if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "end" }));
  }
}