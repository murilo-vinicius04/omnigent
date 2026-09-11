"""The standalone live-voice probe page.

Kept apart from the route module so the markup does not drown the routing,
and served as a string rather than a static file because ``vite build
--emptyOutDir`` erases ``static/web-ui``.

The page answers one question — is talking to this better than what we
have — and it is built around the failure that made an earlier attempt at
this unusable: a session that stops listening without saying so. Hence the
always-visible connection state, the local microphone meter (a moving bar
with no reply means the fault is remote, not the mic), and a watchdog that
calls a silent session dead instead of leaving it looking alive.
"""

from __future__ import annotations

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Live voice probe</title>
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
  main { max-width: 760px; margin: 0 auto; display: flex; flex-direction: column; gap: 16px; }
  h1 { font-size: 18px; margin: 0; font-weight: 600; }
  .sub { color: var(--muted); margin: 0; }
  .panel { background: var(--panel); border: 1px solid var(--line);
           border-radius: 10px; padding: 16px; }
  .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  button { font: inherit; font-weight: 600; border: 0; border-radius: 8px;
           padding: 10px 18px; cursor: pointer; background: var(--live); color: #fff; }
  button.stop { background: var(--bad); }
  button:disabled { opacity: .45; cursor: not-allowed; }
  .badge { display: inline-flex; align-items: center; gap: 8px; padding: 6px 12px;
           border-radius: 999px; border: 1px solid var(--line); font-weight: 600; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--muted); }
  .dot.ok { background: var(--ok); } .dot.warn { background: var(--warn); }
  .dot.bad { background: var(--bad); } .dot.live { background: var(--live); }
  .dot.pulse { animation: pulse 1.1s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: .25; } }
  .meter { height: 10px; background: #10131a; border-radius: 999px;
           overflow: hidden; flex: 1; min-width: 140px; }
  .meter > i { display: block; height: 100%; width: 0%; background: var(--ok);
               transition: width .06s linear; }
  dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 16px; margin: 0; }
  dt { color: var(--muted); } dd { margin: 0; font-variant-numeric: tabular-nums; }
  #log { margin: 0; padding: 12px; background: #10131a; border-radius: 8px;
         max-height: 260px; overflow: auto; font: 12px/1.55 ui-monospace, monospace;
         white-space: pre-wrap; word-break: break-word; }
  .t { color: var(--muted); }
  .err { color: var(--bad); } .good { color: var(--ok); } .note { color: var(--live); }
</style>
</head>
<body>
<main>
  <div>
    <h1>Live voice probe</h1>
    <p class="sub">__MODEL__ &middot; voice __VOICE__ &middot; billed on wall-clock,
       silence included. Stage one: no backend model attached.</p>
  </div>

  <div class="panel row">
    <button id="go">Start talking</button>
    <span class="badge"><span class="dot" id="dot"></span><span id="state">idle</span></span>
    <span class="badge">mic<span class="meter"><i id="mic"></i></span></span>
  </div>

  <div class="panel">
    <dl>
      <dt>session</dt><dd id="elapsed">&mdash;</dd>
      <dt>estimated cost</dt><dd id="cost">$0.0000</dd>
      <dt>connect time</dt><dd id="connect">&mdash;</dd>
      <dt>last event</dt><dd id="last">&mdash;</dd>
    </dl>
  </div>

  <pre id="log"></pre>
  <audio id="remote" autoplay></audio>
</main>

<script>
const RATE = __RATE__, MAX_S = __MAX_S__, CONFIGURED = __CONFIGURED__;
const $ = (id) => document.getElementById(id);
let pc = null, dc = null, stream = null, audioCtx = null, raf = 0;
let startedAt = 0, lastEventAt = 0, ticker = 0, watchdog = 0, connectMs = null;

function log(msg, cls) {
  const t = new Date().toLocaleTimeString();
  const line = document.createElement('span');
  line.className = cls || '';
  line.textContent = `[${t}] ${msg}\\n`;
  $('log').appendChild(line);
  $('log').scrollTop = $('log').scrollHeight;
}

function setState(text, tone, pulse) {
  $('state').textContent = text;
  $('dot').className = 'dot ' + (tone || '') + (pulse ? ' pulse' : '');
}

function tick() {
  if (!startedAt) return;
  const s = (Date.now() - startedAt) / 1000;
  const secs = String(Math.floor(s % 60)).padStart(2, '0');
  $('elapsed').textContent = `${Math.floor(s / 60)}m ${secs}s`;
  $('cost').textContent = '$' + (s / 60 * RATE).toFixed(4);
  if (lastEventAt) {
    $('last').textContent = ((Date.now() - lastEventAt) / 1000).toFixed(1) + 's ago';
  }
  if (s > MAX_S) { log('runaway guard: session exceeded the cap', 'err'); stop('capped'); }
}

// A session that goes quiet while the microphone still shows energy is dead,
// whatever the connection state claims. That is the failure this page exists
// to make visible.
function armWatchdog() {
  clearInterval(watchdog);
  watchdog = setInterval(() => {
    if (!startedAt || !lastEventAt) return;
    const quiet = (Date.now() - lastEventAt) / 1000;
    if (quiet > 20) {
      setState('no reply for ' + quiet.toFixed(0) + 's', 'bad', false);
      log('watchdog: nothing from the server in ' + quiet.toFixed(0) + 's', 'err');
    }
  }, 2000);
}

function meter(track) {
  audioCtx = new AudioContext();
  const src = audioCtx.createMediaStreamSource(new MediaStream([track]));
  const node = audioCtx.createAnalyser();
  node.fftSize = 512;
  src.connect(node);
  const buf = new Uint8Array(node.frequencyBinCount);
  const draw = () => {
    node.getByteTimeDomainData(buf);
    let peak = 0;
    for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
    $('mic').style.width = Math.min(100, (peak / 128) * 260) + '%';
    raf = requestAnimationFrame(draw);
  };
  draw();
}

async function start() {
  if (!CONFIGURED) { log('no OpenAI key configured on the server', 'err'); return; }
  $('go').disabled = true;
  const t0 = performance.now();
  try {
    setState('asking for the microphone', 'warn', true);
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    meter(stream.getAudioTracks()[0]);
    log('microphone open', 'good');

    setState('connecting', 'warn', true);
    pc = new RTCPeerConnection();
    pc.ontrack = (e) => {
      $('remote').srcObject = e.streams[0];
      log('remote audio track', 'good');
    };
    pc.addTrack(stream.getAudioTracks()[0], stream);

    dc = pc.createDataChannel('oai-events');
    dc.onopen = () => log('data channel open', 'good');
    dc.onclose = () => { log('data channel closed', 'err'); setState('closed', 'bad', false); };
    dc.onmessage = (e) => {
      lastEventAt = Date.now();
      let ev; try { ev = JSON.parse(e.data); } catch { log('raw: ' + e.data); return; }
      onEvent(ev);
    };

    pc.onconnectionstatechange = () => {
      log('connection: ' + pc.connectionState, pc.connectionState === 'connected' ? 'good' : '');
      if (pc.connectionState === 'connected') {
        connectMs = Math.round(performance.now() - t0);
        $('connect').textContent = connectMs + ' ms';
        setState('listening', 'ok', false);
      }
      if (['failed', 'disconnected', 'closed'].includes(pc.connectionState)) {
        setState(pc.connectionState, 'bad', false);
      }
    };

    await pc.setLocalDescription(await pc.createOffer());
    // One POST carries the whole offer, so candidates cannot trickle in
    // afterwards: wait for gathering to finish before sending.
    await new Promise((done) => {
      if (pc.iceGatheringState === 'complete') return done();
      pc.onicegatheringstatechange = () => pc.iceGatheringState === 'complete' && done();
      setTimeout(done, 3000);
    });

    const res = await fetch('/v1/live/offer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sdp: pc.localDescription.sdp }),
    });
    if (!res.ok) throw new Error(`handshake ${res.status}: ${(await res.text()).slice(0, 300)}`);
    const answer = await res.json();
    log('session ' + answer.session_id, 'note');
    await pc.setRemoteDescription({ type: 'answer', sdp: answer.sdp });

    startedAt = Date.now(); lastEventAt = Date.now();
    ticker = setInterval(tick, 250);
    armWatchdog();
    $('go').textContent = 'Stop'; $('go').className = 'stop'; $('go').disabled = false;
  } catch (err) {
    log(String(err && err.message || err), 'err');
    setState('failed', 'bad', false);
    stop('error');
  }
}

function onEvent(ev) {
  const t = ev.type || '?';
  if (t.endsWith('.delta') && typeof ev.delta === 'string') return;  // too chatty to log
  if (t === 'error') { log('server error: ' + JSON.stringify(ev), 'err'); return; }
  if (t.includes('speech_started')) setState('you are speaking', 'live', true);
  else if (t.includes('speech_stopped')) setState('thinking', 'warn', true);
  else if (t.includes('audio.done') || t.includes('response.done'))
    setState('listening', 'ok', false);
  else if (t.includes('audio') && t.includes('delta')) setState('speaking', 'live', true);
  if (ev.transcript) log('transcript: ' + ev.transcript, 'note');
  else log(t);
}

function stop(why) {
  clearInterval(ticker); clearInterval(watchdog); cancelAnimationFrame(raf);
  if (startedAt) {
    const s = (Date.now() - startedAt) / 1000;
    log(`session ended (${why}): ${s.toFixed(1)}s, ~$${(s / 60 * RATE).toFixed(4)}`, 'note');
  }
  startedAt = 0;
  try { dc && dc.close(); } catch {}
  try { pc && pc.close(); } catch {}
  try { stream && stream.getTracks().forEach((t) => t.stop()); } catch {}
  try { audioCtx && audioCtx.close(); } catch {}
  pc = dc = stream = audioCtx = null;
  $('mic').style.width = '0%';
  setState('idle', '', false);
  $('go').textContent = 'Start talking'; $('go').className = ''; $('go').disabled = false;
}

$('go').addEventListener('click', () => (startedAt ? stop('you stopped it') : start()));
// The API cannot terminate a session; only the peer connection dropping
// does. A closed tab must not keep billing.
addEventListener('pagehide', () => startedAt && stop('page closed'));
addEventListener('beforeunload', () => startedAt && stop('page closed'));
if (!CONFIGURED) {
  log('no OpenAI key on the server: set OPENAI_API_KEY or ~/.omnigent/openai-key', 'err');
}
</script>
</body>
</html>
"""


def render_test_page(
    *,
    model: str,
    voice: str,
    usd_per_minute: float,
    max_session_s: int,
    configured: bool,
) -> str:
    """Render the probe page with the server's live-voice settings baked in."""
    return (
        _PAGE.replace("__MODEL__", model)
        .replace("__VOICE__", voice)
        .replace("__RATE__", repr(float(usd_per_minute)))
        .replace("__MAX_S__", str(int(max_session_s)))
        .replace("__CONFIGURED__", "true" if configured else "false")
    )
