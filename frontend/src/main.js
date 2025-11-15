
// Utility: log window
const logWin = document.getElementById("logWindow");
function log(msg) {
  logWin.classList.remove("hidden");
  logWin.innerHTML += `<div>${msg}</div>`;
  logWin.scrollTop = logWin.scrollHeight;
}

// Buttons
const modeRecord = document.getElementById("modeRecord");
const modeUpload = document.getElementById("modeUpload");
const formContainer = document.getElementById("formContainer");

// Render UI helpers
function html(strings, ...values) {
  return strings.map((s, i) => s + (values[i] || "")).join("");
}

function showForm(inner) {
  formContainer.classList.remove("hidden");
  formContainer.innerHTML = inner;
}

// Mode: record & transcribe
modeRecord.onclick = () => {
  showForm(html`
    <h2 class="text-lg font-medium mb-3">🎙 Запис & Транскрипция</h2>

    <label class="block mb-3">
      <span class="text-sm text-gray-300">Име на срещата</span>
      <input id="meetingName" class="mt-1 w-full bg-black/20 p-2 rounded" placeholder="Team Sync / Interview / etc">
    </label>

    <label class="block mb-3">
      <span class="text-sm text-gray-300">Тема или контекст на срещата</span>
      <textarea id="meetingTopic" class="mt-1 w-full bg-black/20 p-2 rounded" rows="2"
        placeholder="Планиране, проблеми, идеи..."></textarea>
    </label>

    <label class="block mb-4">
      <span class="text-sm text-gray-300">Брой участници</span>
      <input id="participants" class="mt-1 w-full bg-black/20 p-2 rounded" placeholder="2 / 4 / 10">
    </label>

    <button id="startRecBtn"
      class="w-full py-3 rounded-lg bg-pipe-accent text-black font-medium hover:opacity-90 transition">
      Start recording
    </button>
  `);

  document.getElementById("startRecBtn").onclick = () => {
    log("Recording started...");
    // TODO: integrate getUserMedia streaming logic and send it to the backend with ws
  };
};

// Mode: Upload file
modeUpload.onclick = () => {
  showForm(html`
    <h2 class="text-lg font-medium mb-3">Качване на аудио файл</h2>

    <input id="audioFile" type="file" accept="audio/*"
      class="w-full bg-black/20 p-3 rounded mb-4" />

    <button id="uploadBtn"
      class="w-full py-3 rounded-lg bg-pipe-accent2 text-black font-medium hover:opacity-90 transition">
      Process File
    </button>
  `);

  document.getElementById("uploadBtn").onclick = async () => {
    const file = document.getElementById("audioFile").files[0];
    if (!file) {
      alert("Моля избери файл.");
      return;
    }
    log(`Uploading file: ${file.name} (${file.size} bytes)`);
    // TODO: send chunked upload to backend
  };
};
