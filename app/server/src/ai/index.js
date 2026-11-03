// The one place the web app gets its AI engine from.
//
//   ENGINE=auto  (default) use the real engine through the Python bridge when it
//                answers /health, otherwise the built-in fake engine
//   ENGINE=engine  always the real engine (degraded results while it is down)
//   ENGINE=fake    always the built-in fake engine (ENGINE_FAKE_MODE=normal|slow|degraded)
//
// Every backend has the same surface: run, followUp, getExercise, course,
// addMaterial, removeMaterial, invalidate, health.

const { createFakeEngine } = require("./fake/engine");
const { createHttpEngine } = require("./http-engine");

const HEALTH_EVERY_MS = 15_000;

function createEngine(options = {}) {
  const {
    mode = "auto",
    url = "http://127.0.0.1:8765",
    fakeMode = "normal",
    fakeSpeed,
    log = () => {},
    fetchImpl
  } = options;

  const fake = createFakeEngine({ mode: fakeMode, speed: fakeSpeed });
  const real = mode === "fake" ? null : createHttpEngine({ url, fetchImpl });
  let realUp = false;
  let lastHealth = null;
  let timer = null;

  async function probe() {
    if (!real) return false;
    try {
      lastHealth = await real.health();
      if (!realUp) log(`AI engine connected at ${real.url} (${lastHealth.engine || "engine"})`);
      realUp = true;
    } catch {
      if (realUp) log(`AI engine at ${real.url} stopped answering — ${mode === "auto" ? "using the built-in fake engine" : "results are degraded"}`);
      realUp = false;
    }
    return realUp;
  }

  if (mode === "auto" || mode === "engine") {
    probe();
    timer = setInterval(probe, HEALTH_EVERY_MS);
    timer.unref?.();
  }

  const active = () => (mode === "fake" ? fake : mode === "engine" ? real : realUp ? real : fake);
  const delegate = (name) => (...args) => active()[name](...args);

  return {
    run: delegate("run"),
    followUp: delegate("followUp"),
    getExercise: delegate("getExercise"),
    course: delegate("course"),
    addMaterial: delegate("addMaterial"),
    removeMaterial: delegate("removeMaterial"),
    invalidate: delegate("invalidate"),
    probe,
    status() {
      const backend = active();
      return {
        mode,
        backend: backend.kind, // "engine" or "fake"
        connected: backend === fake ? true : realUp,
        url: real?.url || null,
        fake_mode: backend === fake ? fake.mode : null,
        version: backend === fake ? "fake-1.3" : lastHealth?.version || null,
        tasks: backend === fake ? fake.tasks : lastHealth?.tasks || null
      };
    },
    close() {
      clearInterval(timer);
    }
  };
}

module.exports = { createEngine };
