import "/src/styles/base.css";

// ELEMENTS
const btnStart = document.getElementById("start-recording");
const btnUseFile = document.getElementById("use-file");
const btnContinue = document.getElementById("begin-flow");
const uploadButton = document.getElementById("upload-file-btn");

const backdrop = document.getElementById("backdrop");
const flowContainer = document.getElementById("flow-container");

// Templates
const tmplForm = document.getElementById("tmpl-form-section");
const tmplUpload = document.getElementById("tmpl-file-upload-section");
const tmplStatus = document.getElementById("tmpl-status");

// STATE
let mediaRecorder = null;
let ws = null;
let isRecording = false;

// ------------------------------------------------
// FLOW UTILS
// ------------------------------------------------

function showScreen(template) {
  // Clear old content
  flowContainer.innerHTML = "";

  // Render new
  if (template) {
    const node = template.content.cloneNode(true);
    flowContainer.appendChild(node);
    showBackdrop();
  } else {
    hideBackdrop();
  }
}

function showBackdrop() {
  backdrop.classList.remove("hidden");
  backdrop.classList.add("opacity-100");
}

function hideBackdrop() {
  backdrop.classList.add("hidden");
  backdrop.classList.remove("opacity-100");
  flowContainer.innerHTML = "";
}

// Clicking outside closes everything
backdrop.onclick = () => {
  hideBackdrop();
};

// ------------------------------------------------
// UI HANDLERS
// ------------------------------------------------

// RECORD → FORM
btnStart.onclick = () => {
  showScreen(tmplForm);
};

// UPLOAD FILE → UPLOAD PANEL
btnUseFile.onclick = () => {
  showScreen(tmplUpload);
};

// FORM → CONTINUE → STATUS → RECORDING LOGIC
document.addEventListener("click", async (e) => {
  if (e.target.id === "begin-flow") {
    const name = document.getElementById("meeting-name").value.trim();
    const topic = document.getElementById("meeting-topic").value.trim();
    const participants = document.getElementById("meeting-participants").value.trim();

    if (!name || !topic || !participants) {
      alert("Please fill in all fields.");
      return;
    }

    showScreen(tmplStatus);
    const statusText = document.getElementById("status-text");
    statusText.innerText = "Connecting to audio service…";

    try {
      await startRecordingAndStreaming({ name, topic, participants });
    } catch (err) {
      console.error(err);
      statusText.innerText = "Error starting recording.";
    }
  }
});

// UPLOAD → STATUS (placeholder)
document.addEventListener("click", (e) => {
  if (e.target.id === "upload-file-btn") {
    const file = document.getElementById("audio-file").files?.[0];
    if (!file) {
      alert("Please select a file.");
      return;
    }

    showScreen(tmplStatus);
    const statusText = document.getElementById("status-text");
    statusText.innerText = "Uploading file (placeholder)…";
  }
});

// ------------------------------------------------
// RECORDING + WEBSOCKET STREAMING (UNTouched)
// ------------------------------------------------

async function startRecordingAndStreaming(metadata) {
  ws = new WebSocket("ws://37.27.86.255:8000/ws");
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    console.log("WS connected");
    ws.send(JSON.stringify({ type: "metadata", ...metadata }));

    const text = document.getElementById("status-text");
    if (text) text.innerText = "Recording…";
  };

  ws.onerror = (err) => {
    console.error("WebSocket error:", err);
  };

  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);

    if (data.type === "final_transcript") {
      const text = document.getElementById("status-text");
      if (text) text.innerText = "Processing complete.";
      stopRecording();
    }

    if (data.type === "error") {
      const text = document.getElementById("status-text");
      if (text) text.innerText = "Server error: " + data.message;
      stopRecording();
    }
  };

  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

  mediaRecorder = new MediaRecorder(stream, {
    mimeType: "audio/webm;codecs=opus"
  });

  mediaRecorder.onstart = () => (isRecording = true);

  mediaRecorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
      e.data.arrayBuffer().then((buf) => ws.send(buf));
    }
  };

  mediaRecorder.onstop = () => {
    isRecording = false;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "end" }));
    }
  };

  mediaRecorder.start(300);
}

function stopRecording() {
  if (isRecording && mediaRecorder) mediaRecorder.stop();
  isRecording = false;
}
