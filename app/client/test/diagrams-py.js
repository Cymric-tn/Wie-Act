// The Python the diagram tests build their specs with. No such Python on this machine = those tests are skipped, so a
// front-end-only checkout still runs `npm test`.
import { execFileSync, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const works = (cmd) => spawnSync(cmd, ["-c", "import sympy, pydantic, yaml"], { cwd: ROOT }).status === 0;
const PY = [process.env.PYTHON, process.env.ENGINE_PYTHON, path.join(ROOT, "myenv/bin/python"), "python3", "python"].filter(Boolean).find(works);

export const skip = PY ? false : "no python with sympy, pydantic and pyyaml (set PYTHON)";
export const COURSE = "tests/fixtures/diagrams_course.yaml";

/** Runs `code` after `from diagrams import *` and parses what it prints as JSON. */
export const py = (code, ...args) => JSON.parse(execFileSync(PY, ["-c", `import sys,json;sys.path.insert(0,'app');from diagrams import *;${code}`, ...args], { encoding: "utf8", cwd: ROOT }));
