"""The viewer page, shared by the iOS and Android bridges.

Both platforms end up in the same place -- an H.264 Annex-B stream and a JSON
action endpoint -- so the page is identical apart from the navigation buttons a
platform actually has. Keeping one copy means a fix to the touch mapping or the
decoder cannot land on one side and silently rot on the other.

Placeholders filled by the server: __W__ and __H__ (the device's input
coordinate space), __FPS__, __NAV__ (the navigation button row) and __TITLE__.
"""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<title>__TITLE__</title>
<style>
  :root { --bg:#0b0d10; --panel:#161a1f; --line:#272d35; --fg:#e8ecf1; --dim:#93a0ad; --accent:#4c8dff; }
  * { box-sizing:border-box; -webkit-tap-highlight-color:transparent; }
  html,body { margin:0; height:100%; background:var(--bg); color:var(--fg);
              font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
              overscroll-behavior:none; }
  body { display:flex; flex-direction:column; }
  #stage { flex:1; display:flex; align-items:center; justify-content:center; min-height:0; padding:8px; }
  #screen, #fallback { max-width:100%; max-height:100%; object-fit:contain; touch-action:none;
            border-radius:14px; background:#000; display:block; box-shadow:0 6px 24px rgba(0,0,0,.6); }
  #fallback { display:none; }
  #bar { display:flex; gap:6px; padding:8px; background:var(--panel); border-top:1px solid var(--line);
         align-items:center; padding-bottom:calc(8px + env(safe-area-inset-bottom)); }
  button { background:#222932; color:var(--fg); border:1px solid var(--line); border-radius:9px;
           padding:10px 13px; font-size:15px; cursor:pointer; }
  button:active { background:var(--accent); border-color:var(--accent); }
  #text { flex:1; min-width:0; background:#0e1217; color:var(--fg); border:1px solid var(--line);
          border-radius:9px; padding:10px; font-size:16px; }
  #status { display:flex; gap:10px; padding:4px 10px; font-size:12px; color:var(--dim);
            background:var(--panel); white-space:nowrap; overflow:hidden; }
  #mode { font-weight:600; }
  #mode.h264 { color:#4ade80; } #mode.mjpeg { color:#fbbf24; } #mode.err { color:#ff8080; }
  #msg { overflow:hidden; text-overflow:ellipsis; }
</style>
</head>
<body>
<div id="stage"><canvas id="screen"></canvas><img id="fallback" alt="device screen"></div>
<div id="status"><span id="mode">starting…</span><span id="rate"></span><span id="msg"></span></div>
<div id="bar">
  __NAV__
  <input id="text" placeholder="type into the focused field…" autocapitalize="off" autocorrect="off" spellcheck="false">
  <button id="send">Send</button>
  <button data-key="BACKSPACE">⌫</button>
  <button data-key="ENTER">⏎</button>
</div>
<script>
const canvas = document.getElementById('screen');
const fallback = document.getElementById('fallback');
const ctx = canvas.getContext('2d');
let dev = {width:__W__, height:__H__};
let target = canvas;          // whichever element currently shows the screen
let bytes = 0, frames = 0;

function mode(text, cls) {
  const el = document.getElementById('mode');
  el.textContent = text; el.className = cls || '';
}
function say(m) { document.getElementById('msg').textContent = m || ''; }

setInterval(() => {
  document.getElementById('rate').textContent =
    `${(bytes*8/1e6).toFixed(2)} Mbps · ${frames} fps`;
  bytes = 0; frames = 0;
}, 1000);

/* ---------- input ---------- */
async function post(path, body) {
  try {
    const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                                body: JSON.stringify(body||{})});
    const j = await r.json();
    if (!r.ok || j.error) { say(j.error || ('HTTP ' + r.status)); return null; }
    return j;
  } catch (e) { say(String(e)); return null; }
}

// The picture is letterboxed inside its element by object-fit:contain, so the
// element's box is not the picture's box. Without this correction every touch
// drifts as soon as the phone's aspect ratio differs from the device's.
function pictureBox() {
  const r = target.getBoundingClientRect();
  const nat = dev.width / dev.height;
  const box = r.width / r.height;
  let w = r.width, h = r.height, ox = 0, oy = 0;
  if (box > nat) { w = r.height * nat; ox = (r.width - w) / 2; }
  else           { h = r.width / nat;  oy = (r.height - h) / 2; }
  return {left:r.left + ox, top:r.top + oy, width:w, height:h};
}

function toPoint(ev) {
  const b = pictureBox();
  const fx = (ev.clientX - b.left) / b.width, fy = (ev.clientY - b.top) / b.height;
  return {
    x: Math.max(0, Math.min(dev.width  - 1, Math.round(fx * dev.width))),
    y: Math.max(0, Math.min(dev.height - 1, Math.round(fy * dev.height))),
    inside: fx >= 0 && fx <= 1 && fy >= 0 && fy <= 1
  };
}

// Single finger only. Pinch was wired up here and worked from the server
// side -- idb's HIDPinch, and two tracked pointers over scrcpy's control
// socket -- but behaved badly under real fingers on both platforms, so the
// page no longer emits it. The /pinch endpoint still exists on both backends
// for anyone driving them directly.
//
// Any finger past the first is ignored outright rather than tracked: a second
// touch that is half-handled lands as a stray swipe when it lifts, which is
// worse than doing nothing.
let primary = null;   // pointerId of the finger being followed
let start = null;

function bindTouch(el) {
  el.addEventListener('pointerdown', ev => {
    if (primary !== null) return;        // already have a finger down
    const p = toPoint(ev); if (!p.inside) return;
    primary = ev.pointerId;
    start = {x:p.x, y:p.y, t:Date.now()};
    el.setPointerCapture(ev.pointerId); ev.preventDefault();
  });

  el.addEventListener('pointerup', async ev => {
    if (ev.pointerId !== primary) return;
    const s = start;
    primary = null; start = null;
    if (!s) return;
    ev.preventDefault();

    const p = toPoint(ev), dt = Date.now() - s.t;
    if (Math.hypot(p.x - s.x, p.y - s.y) > 12) {
      say(`swipe ${s.x},${s.y} → ${p.x},${p.y}`);
      await post('/swipe', {x1:s.x, y1:s.y, x2:p.x, y2:p.y, ms:Math.max(100, Math.min(dt, 1500))});
    } else if (dt >= 500) {
      say(`hold ${s.x},${s.y} (${dt}ms)`);
      await post('/tap', {x:s.x, y:s.y, ms:dt});
    } else {
      say(`tap ${s.x},${s.y}`);
      await post('/tap', {x:s.x, y:s.y});
    }
  });

  el.addEventListener('pointercancel', ev => {
    if (ev.pointerId === primary) { primary = null; start = null; }
  });
  el.addEventListener('contextmenu', ev => ev.preventDefault());
}
bindTouch(canvas); bindTouch(fallback);

for (const b of document.querySelectorAll('[data-btn]'))
  b.onclick = () => { say(b.dataset.btn.toLowerCase()); post('/button', {name:b.dataset.btn}); };
for (const b of document.querySelectorAll('[data-key]'))
  b.onclick = () => { say(b.dataset.key.toLowerCase()); post('/key', {name:b.dataset.key}); };

document.getElementById('send').onclick = () => {
  const box = document.getElementById('text');
  if (!box.value) return;
  say(`typing ${box.value.length} chars`);
  post('/text', {text: box.value}); box.value = '';
};
document.getElementById('text').addEventListener('keydown', ev => {
  if (ev.key === 'Enter') { ev.preventDefault(); document.getElementById('send').click(); }
});

/* ---------- H.264 over WebCodecs ---------- */

// Both back ends emit an Annex-B elementary stream: NAL units separated by
// start codes, with no container to say where a picture ends. Split on the
// start codes and hand each picture to the decoder, carrying the parameter
// sets forward so a keyframe can always be decoded standalone.
function splitAnnexB(buf) {
  const nals = []; let i = 0, startOfNal = -1;
  while (i < buf.length - 3) {
    if (buf[i] === 0 && buf[i+1] === 0 &&
        (buf[i+2] === 1 || (buf[i+2] === 0 && buf[i+3] === 1))) {
      const len = buf[i+2] === 1 ? 3 : 4;
      if (startOfNal >= 0) nals.push(buf.subarray(startOfNal, i));
      startOfNal = i; i += len;
    } else i++;
  }
  // Keep the tail: the next read almost always lands mid-NAL.
  return {nals, rest: startOfNal >= 0 ? buf.subarray(startOfNal)
                                      : buf.subarray(Math.max(0, buf.length - 3))};
}

function nalHeaderOffset(nal) { return nal[2] === 1 ? 3 : 4; }

function codecFromSps(sps) {
  // After the start code and NAL header come profile_idc, the constraint flags
  // and level_idc; together they are the avc1 string the decoder expects.
  const o = nalHeaderOffset(sps);
  const hex = b => b.toString(16).padStart(2, '0');
  return 'avc1.' + hex(sps[o+1]) + hex(sps[o+2]) + hex(sps[o+3]);
}

function concat(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0); out.set(b, a.length); return out;
}

async function startH264() {
  if (!('VideoDecoder' in window)) throw new Error('no VideoDecoder');

  const res = await fetch('/h264');
  if (!res.ok || !res.body) throw new Error('stream HTTP ' + res.status);
  const reader = res.body.getReader();

  let configured = false, sawKey = false;
  let sps = null, pps = null, pending = [], ts = 0;
  const step = 1e6 / __FPS__;

  const decoder = new VideoDecoder({
    output: frame => {
      if (canvas.width !== frame.displayWidth) {
        canvas.width = frame.displayWidth; canvas.height = frame.displayHeight;
      }
      ctx.drawImage(frame, 0, 0); frame.close(); frames++;
    },
    error: e => say('decoder: ' + e.message)
  });

  const flush = isKey => {
    if (!pending.length) return;
    let data = new Uint8Array(0);
    if (isKey && sps && pps) data = concat(concat(data, sps), pps);
    for (const n of pending) data = concat(data, n);
    pending = [];
    if (isKey) sawKey = true;
    if (!sawKey) return;        // a decoder cannot start on a delta frame
    try {
      decoder.decode(new EncodedVideoChunk({
        type: isKey ? 'key' : 'delta', timestamp: ts, data
      }));
      ts += step;
    } catch (e) { say('decode: ' + e.message); }
  };

  mode('H.264', 'h264');
  canvas.style.display = 'block'; fallback.style.display = 'none'; target = canvas;
  let buf = new Uint8Array(0);

  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    bytes += value.length;
    buf = concat(buf, value);
    const {nals, rest} = splitAnnexB(buf);
    buf = rest;
    for (const nal of nals) {
      const type = nal[nalHeaderOffset(nal)] & 0x1f;
      if (type === 7) {
        sps = nal;
        if (!configured) {
          const cfg = {codec: codecFromSps(nal), optimizeForLatency: true};
          const ok = await VideoDecoder.isConfigSupported(cfg);
          if (!ok.supported) throw new Error('unsupported ' + cfg.codec);
          decoder.configure(cfg); configured = true;
        }
      }
      else if (type === 8) pps = nal;
      else if (type === 5) { pending.push(nal); flush(true); }
      else if (type === 1) { pending.push(nal); flush(false); }
    }
  }
  throw new Error('stream ended');
}

function startMjpeg(why) {
  mode('MJPEG', 'mjpeg');
  if (why) say(why);
  canvas.style.display = 'none';
  fallback.style.display = 'block';
  target = fallback;
  fallback.src = '/stream?t=' + Date.now();
}

(async function init() {
  try {
    const m = await (await fetch('/meta')).json();
    if (m.width) dev = {width:m.width, height:m.height};
  } catch (e) { /* the server-rendered defaults will do */ }
  try { await startH264(); }
  catch (e) { startMjpeg('H.264 unavailable (' + e.message + '), using MJPEG'); }
})();
</script>
</body>
</html>
"""


def render(title, width, height, fps, nav):
    """Fill the page for one platform. `nav` is the HTML for its button row."""
    return (PAGE.replace("__TITLE__", title)
                .replace("__W__", str(width))
                .replace("__H__", str(height))
                .replace("__FPS__", str(fps))
                .replace("__NAV__", nav)).encode()
