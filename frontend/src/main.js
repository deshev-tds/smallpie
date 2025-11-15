const formRoot = document.getElementById("formRoot");

document.getElementById("recordBtn").addEventListener("click", () => {
  renderForm("record");
});

document.getElementById("uploadBtn").addEventListener("click", () => {
  renderForm("upload");
});

function renderForm(type) {
  const title =
    type === "record"
      ? "Нова среща (запис)"
      : "Нова среща (качен файл)";

  formRoot.innerHTML = `
    <div class="mt-6 bg-white shadow p-5 rounded-xl space-y-4">

      <h3 class="text-xl font-medium text-zinc-800">${title}</h3>

      <div>
        <label class="text-sm text-zinc-500">Име на срещата</label>
        <input id="meetingName" type="text"
          class="w-full mt-1 p-2 rounded-lg border border-zinc-300 focus:ring-2 
                 focus:ring-blue-500 outline-none" />
      </div>

      <div>
        <label class="text-sm text-zinc-500">Тема / Контекст</label>
        <textarea id="meetingTopic"
          class="w-full mt-1 p-2 rounded-lg border border-zinc-300 focus:ring-2 
                 focus:ring-blue-500 outline-none"></textarea>
      </div>

      <div>
        <label class="text-sm text-zinc-500">Участници</label>
        <input id="meetingPeople" type="text"
          class="w-full mt-1 p-2 rounded-lg border border-zinc-300 focus:ring-2 
                 focus:ring-blue-500 outline-none" />
      </div>

      ${type === "upload"
        ? `<input id="fileInput" type="file" accept=".wav,.mp3"
                   class="w-full p-2 border rounded-lg" />`
        : ""
      }

      ${
        type === "record"
          ? `
        <button id="startRecord"
          class="w-full bg-blue-600 text-white p-3 rounded-lg text-center 
                 font-medium hover:bg-blue-700 transition">
          Започни запис
        </button>
        <button id="stopRecord"
          class="w-full bg-red-600 text-white p-3 rounded-lg text-center 
                 font-medium mt-2 hidden hover:bg-red-700 transition">
          Спри запис
        </button>
        `
          : `
        <button id="uploadProceed"
          class="w-full bg-blue-600 text-white p-3 rounded-lg text-center 
                 font-medium hover:bg-blue-700 transition">
          Качи и изпрати
        </button>
        `
      }
    </div>
  `;

  if (type === "record") initRecordLogic();
  if (type === "upload") initUploadLogic();
}

function initRecordLogic() {
  const startBtn = document.getElementById("startRecord");
  const stopBtn = document.getElementById("stopRecord");

  let mediaRecorder;
  let chunks = [];

  startBtn.onclick = async () => {
    chunks = [];

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream);

    mediaRecorder.start();

    startBtn.classList.add("hidden");
    stopBtn.classList.remove("hidden");

    mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
  };

  stopBtn.onclick = async () => {
    mediaRecorder.stop();
    stopBtn.classList.add("hidden");
    startBtn.classList.remove("hidden");

    await new Promise((res) => {
      mediaRecorder.onstop = res;
    });

    const blob = new Blob(chunks, { type: "audio/webm" });

    uploadToBackend(blob);
  };
}

function initUploadLogic() {
  document.getElementById("uploadProceed").onclick = async () => {
    const file = document.getElementById("fileInput").files[0];
    if (!file) return alert("Качи файл!");

    uploadToBackend(file);
  };
}

async function uploadToBackend(blobOrFile) {
  alert("Тук ще викаме backend API-то на smallpie v0.6 :)");
}
