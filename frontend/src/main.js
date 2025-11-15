import "/src/styles/base.css";

// UI ELEMENTS
const btnStart = document.getElementById("start-recording");
const btnUseFile = document.getElementById("use-file");
const formSection = document.getElementById("form-section");
const fileSection = document.getElementById("file-upload-section");
const btnContinue = document.getElementById("begin-flow");
const uploadButton = document.getElementById("upload-file-btn");
const statusBox = document.getElementById("status");
const statusText = document.getElementById("status-text");

// Recording & WebSocket state
let mediaRecorder = null;
let ws = null;
let isRecording = false;

// -----------------------------------------
// UI BEHAVIOR
// -----------------------------------------
btnStart.onclick = () => {
  formSection.classList.remove("hidden");
  fileSection.classList.add("hidden");
};

btnUseFile.onclick = () => {
  fileSection.classList.remove("hidden");
  formSection.classList.add("hidden");
};

btnContinue.onclick = async () => {
  const name = document.getElementById("meeting-name").value.trim();
  const topic = document.getElementById("meeting-topic").value.trim();
  const participants = document.getElementById("meeting-participants").value.trim();

  if (!name || !topic || !participants) {
    alert("Please fill in all fields.");
    return;
  }

  statusBox.classList.remove("hidden");
  statusText.innerText = "Connecting to audio service…";

  try {
    await startRecordingAndStreaming({ name, topic, participants });
  } catch (err) {
    console.error(err);
    statusText.innerText = "Error starting recording.";
  }
};

uploadButton.onclick = () => {
  const file = document.getElementById("audio-file").files[0];
  if (!file) {
    alert("Please select a file.");
    return;
  }

  statusBox.classList.remove("hidden");
  statusText.innerText = "Uploading file (placeholder)…";
};

// ------------------------------------------------
// MAIN LOGIC: Recording + WebSocket Streaming
// ------------------------------------------------

async function startRecordingAndStreaming(metadata) {
  // 1) CONNECT TO YOUR BACKEND WS
  ws = new WebSocket("ws://37.27.86.255:8000/ws");
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    console.log("WS connected");
    ws.send(JSON.stringify({ type: "metadata", ...metadata }));
    statusText.innerText = "Recording…";
  };

  ws.onerror = (err) => {
    console.error("WebSocket error:", err);
  };

  ws.onmessage = (msg) => {
    const data = JSON.parse(msg.data);

    if (data.type === "final_transcript") {
      statusText.innerText = "Processing complete.";
      stopRecording();
    }

    if (data.type === "error") {
      statusText.innerText = "Server error: " + data.message;
      stopRecording();
    }
  };

  // 2) ASK FOR MIC PERMISSION
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

  // 3) SETUP MEDIA RECORDER
  mediaRecorder = new MediaRecorder(stream, {
    mimeType: "audio/webm;codecs=opus"
  });

  mediaRecorder.onstart = () => {
    isRecording = true;
  };

  // WHILE RECORDING, SEND CHUNKS
  mediaRecorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
      e.data.arrayBuffer().then((buf) => {
        ws.send(buf);
      });
    }
  };

  mediaRecorder.onstop = () => {
    isRecording = false;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "end" }));
    }
  };

  // 4) START RECORDING WITH CHUNKS EVERY 300ms
  mediaRecorder.start(300);
}

function stopRecording() {
  if (isRecording && mediaRecorder) {
    mediaRecorder.stop();
  }
  isRecording = false;
}
