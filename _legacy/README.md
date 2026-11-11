# _legacy

Pre-engine prototype, kept for reference (moved here at step 7, nothing deleted).
Replaced by: `gatekeeper/` (gateway.py), `engine/` + `prompts/*.md` (triage.py, prompts.py),
`engine/language.py` (lang.py), `tests/` (test_gateway.py). `app.py` is feature-layer/UI work and
out of scope for this repo. The files import each other, so run them from inside this folder.

`index-courses-2026-11-11/analyse.json`: the live course index the app (`app/dev.sh --real`) wrote to
`data/index/courses/` during the 2026-11-11 FelCloud slot, holding the chunks of the uploaded test materials
(speed notes, exo7 determinants, the scanned determinants PDF). Moved because `tests/test_ingest_ocr.py` requires that
folder to be absent.
