import "./styles/base.css";
const btnStart = document.getElementById("start-recording");
const btnUseFile = document.getElementById("use-file");
const formSection = document.getElementById("form-section");
const fileSection = document.getElementById("file-upload-section");
const btnContinue = document.getElementById("begin-flow");
const uploadButton = document.getElementById("upload-file-btn");
const statusBox = document.getElementById("status");
const statusText = document.getElementById("status-text");

btnStart.onclick = () => {
  formSection.classList.remove("hidden");
  fileSection.classList.add("hidden");
};

btnUseFile.onclick = () => {
  fileSection.classList.remove("hidden");
  formSection.classList.add("hidden");
};

btnContinue.onclick = () => {
  const name = document.getElementById("meeting-name").value.trim();
  const topic = document.getElementById("meeting-topic").value.trim();
  const participants = document.getElementById("meeting-participants").value.trim();

  if (!name || !topic || !participants) {
    alert("Please fill in all fields.");
    return;
  }

  statusBox.classList.remove("hidden");
  statusText.innerText = "Recording will start soon (placeholder).";
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
