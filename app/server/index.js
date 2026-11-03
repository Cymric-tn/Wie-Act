const fs = require("fs");
const path = require("path");
const { createServer } = require("./src/app");

// Origins allowed to connect. Override with CLIENT_ORIGINS="http://a:5173,http://b:5173".
const origins = (process.env.CLIENT_ORIGINS ||
  "http://localhost:5173,http://127.0.0.1:5173").split(",").map((s) => s.trim());

// Boards are stored here and survive restarts. Override with DB_PATH.
const dbPath = process.env.DB_PATH || path.join(__dirname, "data", "whiteboard.db");
if (dbPath !== ":memory:") fs.mkdirSync(path.dirname(dbPath), { recursive: true });

// AI engine: ENGINE=auto|engine|fake, ENGINE_URL (the Python bridge), ENGINE_FAKE_MODE=normal|slow|degraded.
const ai = {
  mode: process.env.ENGINE || "auto",
  url: process.env.ENGINE_URL || "http://127.0.0.1:8765",
  fakeMode: process.env.ENGINE_FAKE_MODE || "normal",
  allowForce: process.env.ENGINE_ALLOW_FORCE ? process.env.ENGINE_ALLOW_FORCE === "true" : undefined
};

const server = createServer({ dbPath, origins, ai });
const PORT = Number(process.env.PORT) || 3000;

server.listen(PORT).then((port) => {
  console.log(`EdNova server listening on port ${port} (data: ${dbPath}, AI engine: ${ai.mode})`);
});

async function shutdown() {
  await server.close();
  process.exit(0);
}
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
