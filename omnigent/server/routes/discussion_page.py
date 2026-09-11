"""The standalone companion probe page.

Kept apart from the route module so the markup does not drown the
routing, and served as a string rather than a static file because
``vite build --emptyOutDir`` erases ``static/web-ui``.

The page exists to make one invisible thing visible: *what the companion
knows*. The design decision behind this whole feature is that it knows
little on purpose — what Claude is doing and the spoken summaries, never
the transcript — and a claim like that is worth nothing unless you can
see the ledger. So the ledger is the left half of the screen, it
updates on every exchange, and the notes box lets you feed it the same
way the server will once this is wired to a real session.
"""

from __future__ import annotations

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Companion probe</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #14161a; --panel: #1c1f26; --line: #2c313b;
    --text: #e6e8ec; --muted: #939aa7;
    --ok: #4ba97a; --warn: #d9a441; --bad: #d2685f; --live: #5b8cd6;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px 16px; background: var(--bg); color: var(--text);
         font: 14px/1.5 ui-sans-serif, system-ui, sans-serif; }
  main { max-width: 1040px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }
  h1 { font-size: 18px; margin: 0; font-weight: 600; }
  h2 { font-size: 13px; margin: 0 0 10px; font-weight: 600; color: var(--muted);
       text-transform: uppercase; letter-spacing: .04em; }
  .sub { color: var(--muted); margin: 0; }
  .panel { background: var(--panel); border: 1px solid var(--line);
           border-radius: 10px; padding: 16px; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
  @media (max-width: 820px) { .cols { grid-template-columns: 1fr; } }
  .row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  button { font: inherit; font-weight: 600; border: 0; border-radius: 8px;
           padding: 9px 16px; cursor: pointer; background: var(--live); color: #fff; }
  button.ghost { background: #262b34; color: var(--text); }
  button[disabled] { opacity: .5; cursor: default; }
  input, select, textarea { font: inherit; background: #10131a; color: var(--text);
           border: 1px solid var(--line); border-radius: 8px; padding: 9px 10px; }
  input, textarea { flex: 1; min-width: 0; }
  .badge { display: inline-flex; align-items: center; gap: 7px; padding: 5px 11px;
           border-radius: 999px; border: 1px solid var(--line); font-weight: 600; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); }
  .dot.ok { background: var(--ok); } .dot.warn { background: var(--warn); }
  .dot.bad { background: var(--bad); }
  #ledger { margin: 0; padding: 0; list-style: none; display: flex;
            flex-direction: column; gap: 8px; max-height: 58vh; overflow-y: auto; }
  #ledger li { display: grid; grid-template-columns: 88px 1fr; gap: 10px;
               padding: 8px 10px; background: #10131a; border-radius: 8px;
               border-left: 3px solid var(--line); }
  #ledger li.activity { border-left-color: var(--warn); }
  #ledger li.summary { border-left-color: var(--ok); }
  #ledger li.question { border-left-color: var(--live); }
  #ledger li.answer { border-left-color: #7d6bd0; }
  #ledger li.pending { opacity: .62; border-style: dashed; }
  .k { color: var(--muted); font-size: 12px; font-weight: 600; }
  .empty { color: var(--muted); font-style: italic; }
  #log { margin: 0; padding: 12px; background: #10131a; border-radius: 8px;
         max-height: 220px; overflow-y: auto; white-space: pre-wrap;
         font: 12px/1.55 ui-monospace, monospace; }
  .t { color: var(--muted); } .err { color: var(--bad); } .good { color: var(--ok); }
  dl { display: grid; grid-template-columns: auto auto; gap: 4px 14px; margin: 12px 0 0; }
  dt { color: var(--muted); } dd { margin: 0; font-variant-numeric: tabular-nums; }
</style>
</head>
<body>
<main>
  <div>
    <h1>Companion probe</h1>
    <p class="sub">One warm <code>agy</code> process for this whole page, the way one
      will run per Omnigent session. It knows what you tell it below &mdash; nothing else.</p>
  </div>

  <div class="panel">
    <div class="row">
      <span class="badge"><i class="dot" id="dot"></i><span id="state">cold</span></span>
      <span class="sub" id="model">&nbsp;</span>
      <span style="flex:1"></span>
      <button id="warm" class="ghost">Prewarm</button>
      <button id="drop" class="ghost">Kill process</button>
    </div>
    <dl>
      <dt>Last answer</dt><dd id="lastMs">&mdash;</dd>
      <dt>Turns</dt><dd id="turns">0</dd>
      <dt>Undelivered notes</dt><dd id="pending">0</dd>
    </dl>
  </div>

  <div class="cols">
    <div class="panel">
      <h2>What it knows</h2>
      <ul id="ledger"><li class="empty">Nothing yet.</li></ul>
    </div>

    <div style="display:flex; flex-direction:column; gap:16px;">
      <div class="panel">
        <h2>Talk to it</h2>
        <div class="row">
          <input id="say" placeholder="What's going on?" autocomplete="off">
          <button id="send">Ask</button>
        </div>
      </div>
      <div class="panel">
        <h2>Feed it context (free &mdash; no round trip)</h2>
        <div class="row">
          <select id="kind">
            <option value="activity">Claude is doing</option>
            <option value="summary">Claude said</option>
          </select>
          <input id="noteText" placeholder="running the test suite" autocomplete="off">
          <button id="note" class="ghost">Note</button>
        </div>
        <p class="sub" style="margin-top:10px">Notes wait in the ledger and ride the next
          question, so narrating costs nothing until you actually ask something.</p>
      </div>
      <div class="panel">
        <h2>Log</h2>
        <pre id="log"></pre>
      </div>
    </div>
  </div>
</main>

<script>
const SESSION = "probe-" + Math.random().toString(36).slice(2, 10);
const BASE = "/v1/discussion/" + SESSION;
const $ = (id) => document.getElementById(id);
let turns = 0;

function log(msg, cls) {
  const t = new Date().toLocaleTimeString();
  const line = document.createElement("span");
  line.className = cls || "";
  line.textContent = `[${t}] ${msg}\\n`;
  $("log").appendChild(line);
  $("log").scrollTop = $("log").scrollHeight;
}

const LABEL = { activity: "Claude is", summary: "Claude said",
                question: "You asked", answer: "It said", note: "Note" };

function render(state) {
  $("state").textContent = state.running ? (state.warm_since ? "warm" : "starting") : "cold";
  $("dot").className = "dot " + (state.warm_since ? "ok" : state.running ? "warn" : "");
  $("model").textContent = state.model;
  $("pending").textContent = state.pending_notes;
  const list = $("ledger");
  list.textContent = "";
  if (!state.context.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "Nothing yet.";
    list.appendChild(li);
    return;
  }
  // The tail of the ledger that has not reached the process yet is the
  // interesting part: it is the context it will pick up on the next ask.
  const undelivered = state.pending_notes;
  const firstPending = state.context.length - undelivered;
  state.context.forEach((entry, i) => {
    const li = document.createElement("li");
    li.className = entry.kind + (i >= firstPending && undelivered ? " pending" : "");
    const k = document.createElement("span");
    k.className = "k";
    k.textContent = LABEL[entry.kind] || entry.kind;
    const v = document.createElement("span");
    v.textContent = entry.text;
    li.append(k, v);
    list.appendChild(li);
  });
  list.scrollTop = list.scrollHeight;
}

async function call(path, body) {
  const res = await fetch(BASE + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let data = {};
  try { data = JSON.parse(text); } catch (e) { /* keep the raw text below */ }
  if (!res.ok) throw new Error(data.detail || text.slice(0, 200) || res.status);
  return data;
}

function busy(on) {
  for (const id of ["send", "warm", "drop", "note"]) $(id).disabled = on;
}

async function ask() {
  const text = $("say").value.trim();
  if (!text) return;
  $("say").value = "";
  busy(true);
  const t0 = performance.now();
  try {
    const data = await call("/ask", { text });
    const ms = Math.round(performance.now() - t0);
    $("lastMs").textContent = ms + " ms";
    $("turns").textContent = ++turns;
    log(`answered in ${ms} ms`, "good");
    render(data.state);
  } catch (err) {
    log("ask failed: " + err.message, "err");
    refresh();
  } finally {
    busy(false);
    $("say").focus();
  }
}

async function refresh() {
  try { render(await call("")); } catch (err) { log("state failed: " + err.message, "err"); }
}

$("send").onclick = ask;
$("say").onkeydown = (e) => { if (e.key === "Enter") ask(); };
$("noteText").onkeydown = (e) => { if (e.key === "Enter") $("note").click(); };

$("note").onclick = async () => {
  const text = $("noteText").value.trim();
  if (!text) return;
  $("noteText").value = "";
  try {
    render(await call("/note", { kind: $("kind").value, text }));
    log("noted (nothing sent to the model yet)");
  } catch (err) { log("note failed: " + err.message, "err"); }
};

$("warm").onclick = async () => {
  busy(true);
  const t0 = performance.now();
  try {
    render(await call("/prewarm", {}));
    log(`prewarmed in ${Math.round(performance.now() - t0)} ms`, "good");
  } catch (err) {
    log("prewarm failed: " + err.message, "err");
  } finally { busy(false); }
};

$("drop").onclick = async () => {
  try {
    render(await call("/close", {}));
    log("process killed — the ledger survives, ask again to see it replayed");
  } catch (err) { log("close failed: " + err.message, "err"); }
};

// A warm process bills nothing, but it does hold a subprocess; drop it
// when the tab goes away rather than waiting for the idle reaper.
for (const ev of ["pagehide", "beforeunload"]) {
  window.addEventListener(ev, () => {
    navigator.sendBeacon(BASE + "/close", new Blob(["{}"], { type: "application/json" }));
  });
}

log("session " + SESSION);
refresh();
$("say").focus();
</script>
</body>
</html>
"""


def render_test_page() -> str:
    """Return the companion probe page.

    :returns: A complete standalone HTML document.
    """
    return _PAGE
