// The one place the web app gets its AI engine from: the Python bridge (app/bridge/bridge.py), which hosts the real
// Lumi engine, or the engine's own FakeEngine when the bridge is started with --fake. There is no built-in
// fallback: when the bridge is down, results are degraded ("service_unavailable") and the UI says so.
//
// The surface: run, generatePage, followUp, getExercise, course, addMaterial, removeMaterial, confirmChunk, invalidate,
// metrics, health. status() tells the UI which backend answers (engine | fake) and whether it is connected.

const { createHttpEngine } = require("./http-engine");

const HEALTH_EVERY_MS = 15_000;

function createEngine(options = {}) {
  const {
    url = "http://127.0.0.1:8765",
    token = process.env.ENGINE_SERVICE_TOKEN || "",
    log = () => {},
    fetchImpl
  } = options;

  const real = createHttpEngine({ url, token, fetchImpl });
  let up = false;
  let lastHealth = null;
  let lastError = null;

  async function probe() {
    try {
      lastHealth = await real.health();
      lastError = null;
      if (!up) log(`AI engine connected at ${real.url} (${lastHealth.engine || "engine"} ${lastHealth.version || ""})`);
      up = true;
    } catch (error) {
      lastError = error.message;
      if (up) log(`AI engine at ${real.url} stopped answering: ${error.message}`);
      up = false;
    }
    return up;
  }

  // probe every second until the bridge answers (it starts a moment after the server), then every HEALTH_EVERY_MS
  let timer = null;
  let closed = false;
  const tick = async () => {
    await probe();
    if (!closed) {
      timer = setTimeout(tick, up ? HEALTH_EVERY_MS : 1000);
      timer.unref?.();
    }
  };
  tick();

  return {
    ...real,
    probe,
    status() {
      const fake = Boolean(lastHealth?.fake);
      return {
        mode: "engine",
        backend: fake ? "fake" : "engine",
        connected: up,
        url: real.url,
        error: up ? null : lastError,
        version: lastHealth?.version || null,
        tasks: lastHealth?.tasks || null,
        window: lastHealth?.status?.window || null
      };
    },
    close() {
      closed = true;
      clearTimeout(timer);
    }
  };
}

module.exports = { createEngine };
