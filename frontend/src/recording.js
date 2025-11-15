export class Recorder {
  constructor(options = {}) {
    this.mediaStream = null;
    this.mediaRecorder = null;
    this.onChunk = options.onChunk || (() => {});
  }

  async start() {
    this.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: true
    });

    this.mediaRecorder = new MediaRecorder(this.mediaStream, {
      mimeType: "audio/webm;codecs=opus" 
    });

    this.mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        this.onChunk(event.data);
      }
    };

    this.mediaRecorder.start(250); // 250 ms chunk
  }

  stop() {
    return new Promise((resolve) => {
      this.mediaRecorder.onstop = () => resolve();
      this.mediaRecorder.stop();
      this.mediaStream.getTracks().forEach(t => t.stop());
    });
  }
}
