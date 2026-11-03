// Copies the EdNova experience pages into public/ednova/ and wires them to the
// study board. The originals are never modified; re-run after they change:
//   node scripts/sync-ednova.mjs [path/to/EdNova-Dark-Experiences-FINAL]
// Defaults to ednova/design/EdNova-Dark-Experiences-FINAL.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const source = resolve(process.argv[2] || join(here, "../../../design/EdNova-Dark-Experiences-FINAL"));
const target = join(here, "../public/ednova");
mkdirSync(target, { recursive: true });

const shared = `
.ednova-board-cta{border:0;border-radius:13px;padding:12px 15px;color:#fff;font-weight:700;font-size:12px;cursor:pointer;
background:linear-gradient(110deg,#2bb7a0,#1f8fb8);box-shadow:0 5px 20px #2bb7a033}
.ednova-board-cta:hover{filter:brightness(1.08)}
a.nav{text-decoration:none}`;

const constellation = `
<!-- EdNova study-board integration (added by scripts/sync-ednova.mjs) -->
<style>${shared}
.bottom{grid-template-columns:1.05fr 1.1fr auto}
</style>
<script>
(function () {
  const boardUrl = (room) => "/board?room=" + encodeURIComponent(room);

  // Sidebar entry for the board, kept pointing at the selected course.
  const link = document.createElement("a");
  link.className = "nav";
  link.innerHTML = "✎<small>Board</small>";
  document.getElementById("navPlanner").after(link);

  // A clear call to action next to the course details.
  const cta = document.createElement("button");
  cta.className = "ednova-board-cta";
  document.querySelector(".actions").prepend(cta);

  function sync() {
    link.href = boardUrl(selected);
    link.title = "Open the " + selected + " study board";
    cta.textContent = "✎ Open " + selected + " study board";
  }
  cta.onclick = () => (location.href = boardUrl(selected));

  const original = choose;
  choose = function (id) {
    original(id);
    sync();
  };

  // The Planetary Universe is now served alongside this page.
  const universe = () => (location.href = "/ednova/universe.html#universe");
  $("enter").textContent = "✧ Enter Planetary Universe";
  $("enter").onclick = universe;
  $("navUniverse").onclick = universe;
  $("navHome").onclick = () => (location.href = "/app");

  const requested = (new URLSearchParams(location.search).get("course") || "").toUpperCase();
  if (requested && byId(requested)) choose(requested);
  else sync();
})();
</script>
`;

const universe = `
<!-- EdNova study-board integration (added by scripts/sync-ednova.mjs) -->
<style>${shared}
.concept-panel .ednova-board-cta{width:100%;margin-top:10px}
</style>
<script>
(function () {
  const boardUrl = (room) => "/board?room=" + encodeURIComponent(room);

  // The "Learning Room (coming soon)" slot becomes the live study board,
  // and the Academic Atlas gets a home in the sidebar too.
  const navButtons = [...document.querySelectorAll(".sidebar .nav button")];
  const learning = navButtons.find((b) => /Learning Room/.test(b.textContent));
  if (learning) {
    learning.title = "Collaborative study board";
    learning.innerHTML = '✎ <span class="word">Study Board</span>';
    learning.onclick = () => (location.href = "/board");
  }
  const atlas = document.createElement("button");
  atlas.type = "button";
  atlas.title = "Academic Constellation";
  atlas.innerHTML = '✧ <span class="word">Academic Atlas</span>';
  atlas.onclick = () => (location.href = "/ednova/constellation.html");
  (learning || navButtons[navButtons.length - 1]).after(atlas);

  // From any concept, start a shared board for its chapter.
  const study = document.createElement("button");
  study.className = "ednova-board-cta";
  study.id = "studyBoard";
  document.getElementById("tutor").after(study);
  const room = () => courses[ci].name + " · " + courses[ci].chapters[chapter][0];
  const label = () => (study.textContent = "✎ Study “" + courses[ci].chapters[chapter][0] + "” together");
  study.onclick = () => (location.href = boardUrl(room()));
  new MutationObserver(label).observe(document.getElementById("conceptName"), { childList: true });
  label();

  if (location.hash === "#universe") document.getElementById("navConstellation").click();
})();
</script>
`;

function inject(file, out, snippet) {
  const html = readFileSync(join(source, file), "utf8");
  const at = html.lastIndexOf("</body>");
  if (at === -1) throw new Error(`${file}: no </body> found`);
  writeFileSync(join(target, out), html.slice(0, at) + snippet + html.slice(at));
  console.log(`✓ ${out}`);
}

inject("EdNova-Academic-Constellation-FINAL.html", "constellation.html", constellation);
inject("EdNova-Planetary-Universe-FINAL.html", "universe.html", universe);
