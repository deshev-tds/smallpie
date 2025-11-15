import { WebSocketServer } from "ws";
import http from "http";

const server = http.createServer();
const wss = new WebSocketServer({ server, path: "/ws/audio" });

wss.on("connection", (ws) => {
  console.log("client connected to audio stream");

  ws.on("message", (data) => {
    console.log("chunk length:", data.byteLength);

    // TODO Тук имаме опция да forward-нем към Whisper
    // или да буферираме и после да пускаме.
  });

  ws.on("close", () => {
    console.log("client disconnected");
  });
});

server.listen(8080, () => {
  console.log("WS server listening on :8080");
});
