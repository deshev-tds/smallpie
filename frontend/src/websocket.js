export class AudioWebSocket {
  constructor(url) {
    this.url = url;
    this.ws = null;
  }

  connect() {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.url);

      this.ws.binaryType = "arraybuffer";

      this.ws.onopen = () => resolve();
      this.ws.onerror = (err) => reject(err);
    });
  }

  sendChunk(blob) {
    blob.arrayBuffer().then((buf) => {
      this.ws.send(buf);
    });
  }

  onMessage(callback) {
    this.ws.onmessage = (msg) => callback(msg.data);
  }

  close() {
    if (this.ws) this.ws.close();
  }
}
