// The one place the web app gets its AI engine from: the Python bridge (app/bridge/bridge.py), which hosts the real
// EdNova engine, or the engine's own FakeEngine when the bridge is started with --fake. There is no built-in
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

  probe();
  const timer = setInterval(probe, HEALTH_EVERY_MS);
  timer.unref?.();

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
      clearInterval(timer);
    }
  };
}

module.exports = { createEngine };
