import "/src/styles/base.css";

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
  flowContainer.innerHTML = "";
}

function showScreen(template, { showBackdropFlag = true } = {}) {
  clearFlow();

  if (template) {
    const node = template.content.cloneNode(true);
    flowContainer.appendChild(node);
    if (showBackdropFlag) showBackdrop();
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
// DYNAMIC HANDLERS (inside templates)
// ------------------------------------------------

function wireDynamicHandlers() {
  // Form bottom sheet
  const btnBegin = document.getElementById("begin-flow");
  if (btnBegin) {
    btnBegin.onclick = async () => {
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
      if (statusSubtext) statusSubtext.innerText = "We’re preparing to record your meeting in real time.";

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

  if (dropzone) {
    // Drag & drop visual cues
    ["dragenter", "dragover"].forEach((ev) => {
      dropzone.addEventListener(ev, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropzone.classList.add("ring-2", "ring-gold", "bg-bgSubtle", "dark:bg-darkBgSoft");
      });
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
      }
    });

    dropzone.addEventListener("click", () => {
      if (audioInput) audioInput.click();
    });
  }

  if (audioInput) {
    audioInput.onchange = () => {
      const file = audioInput.files?.[0];
      if (file) droppedFile = file;
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

      // TODO: hook real upload logic here
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
    stopRecording();
  }
};

// UPLOAD CARD
btnUseFile.onclick = () => {
  currentMode = "upload";
  showScreen(tmplUpload, { showBackdropFlag: false });
};

// ------------------------------------------------
// RECORDING + WEBSOCKET STREAMING
// ------------------------------------------------

async function startRecordingAndStreaming(metadata) {
  setRecordingState("idle");
  droppedFile = null; // irrelevant here

  ws = new WebSocket("wss://api.smallpie.fun/ws");
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
      if (statusText) statusText.innerText = "All done.";
      if (statusSubtext) statusSubtext.innerText = "Your transcript and notes have been generated.";
      setRecordingState("finished");
      stopRecording();
    }

    if (data.type === "error") {
      const statusText = document.getElementById("status-text");
      const statusSubtext = document.getElementById("status-subtext");
      if (statusText) statusText.innerText = "Server error.";
      if (statusSubtext) statusSubtext.innerText = data.message || "Something went wrong on our side.";
      setRecordingState("error");
      stopRecording();
    }
  };

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

    mediaRecorder = new MediaRecorder(stream, {
      mimeType: "audio/ogg;codecs=opus"
    });

    mediaRecorder.onstart = () => {
      // already handled via ws.onopen; keep here for safety
    };

    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0 && ws?.readyState === WebSocket.OPEN) {
        e.data.arrayBuffer().then((buf) => ws.send(buf));
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
    if (statusSubtext) statusSubtext.innerText = "We can’t access your microphone. Check permissions and try again.";
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
