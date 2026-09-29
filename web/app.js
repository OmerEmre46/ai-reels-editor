/* AI Reels Kurgu Stüdyosu — bağımsız (bağımlılıksız) istemci.
 *
 * Zaman uzayları:
 *  - Sohbet isteklerindeki saniyeler ve oynatıcı zamanı  = KURGULANMIŞ video zamanı.
 *  - EDL (katman verisi, /manual-edl gövdesi)             = HAM video zamanı.
 * Katman listesi kullanıcıya kurgulanmış zamanı gösterir; bunun için EDL'deki
 * kesimlerden aynı haritalamayı (services/timeline.py) burada yeniden hesaplarız.
 */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const MAX_UPLOAD = 500 * 1024 * 1024;
const ALLOWED_EXT = [".mp4", ".mov", ".m4v", ".webm"];
const QUICK_ACTIONS = [
  ["Boşlukları Kes", "Sessiz boşlukları ve duraksamaları kes."],
  ["Enerjik Müzik Ekle", "Enerjik, hızlı tempolu bir müzik ekle; sesi konuşmayı bastırmayacak kadar düşük olsun."],
  ["Vurgulara Zoom + SFX Ekle", "Vurgu anlarına kısa zoom ve uygun ses efektleri ekle."],
  ["Müziği Biraz Kıs", "Müziğin sesini biraz kıs."],
];

const chatEmptyEl = document.getElementById("chatEmpty");

const state = {
  project: null,     // GET /api/projects/{id} yanıtı
  preview: null,     // önizlenen eski sürüm no (null = güncel)
  tab: "edited",
  busy: false,
  working: null,     // {label, t0}
  local: [],         // sunucuda kayıtlı olmayan sohbet mesajları {after, role, text, error}
  bgm: [],
  file: null,
};

/* ---------------- yardımcılar ---------------- */
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
const fmt = (t) => (Math.round(t * 10) / 10).toFixed(1);
const niceName = (n) => {
  const s = n.replace(/-\d{5,}$/, "").replace(/[-_]+/g, " ").trim();
  return s.length > 28 ? s.slice(0, 27) + "…" : s;
};

let toastTimer = null;
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isError ? 6500 : 3500);
}

function detailText(d, status) {
  const x = d && d.detail;
  if (typeof x === "string") return x;
  if (Array.isArray(x)) return x.map((e) => e.msg).join("; ");
  return `Sunucu hatası (${status})`;
}
async function api(path, { method = "GET", json } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: json !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: json !== undefined ? JSON.stringify(json) : undefined,
    });
  } catch {
    throw new Error("Sunucuya bağlanılamadı.");
  }
  let data = null;
  try { data = await res.json(); } catch { /* gövde yok */ }
  if (!res.ok) throw new Error(detailText(data, res.status));
  return data;
}

/* ---------------- zaman haritalama (ham -> kurgulanmış) ---------------- */
function buildCuts(edl, srcDur) {
  let segs = (edl.keep_segments || [])
    .filter((s) => Number.isFinite(s.start) && Number.isFinite(s.end) && s.end > s.start)
    .map((s) => [Math.max(0, s.start), Math.min(srcDur, s.end)])
    .filter((s) => s[1] - s[0] > 0.1)
    .sort((a, b) => a[0] - b[0]);
  if (!segs.length) segs = [[0, srcDur]];
  const merged = [segs[0].slice()];
  for (const [a, b] of segs.slice(1)) {
    if (a <= merged.at(-1)[1]) merged.at(-1)[1] = Math.max(merged.at(-1)[1], b);
    else merged.push([a, b]);
  }
  let o = 0;
  return merged.map(([a, b]) => { const c = { a, b, o }; o += b - a; return c; });
}
function mapPoint(cuts, t, snap = 0.3) {
  for (const c of cuts) if (t >= c.a - 1e-6 && t <= c.b + 1e-6) return c.o + Math.min(Math.max(t - c.a, 0), c.b - c.a);
  if (t < cuts[0].a) return cuts[0].a - t <= snap ? 0 : null;
  const last = cuts.at(-1);
  if (t > last.b) return t - last.b <= snap ? last.o + (last.b - last.a) : null;
  for (let i = 1; i < cuts.length; i++) {
    const p = cuts[i - 1], n = cuts[i];
    if (t > p.b && t < n.a) return Math.min(t - p.b, n.a - t) <= snap ? p.o + (p.b - p.a) : null;
  }
  return null;
}
function mapRange(cuts, a, b) {
  const out = [];
  for (const c of cuts) {
    const lo = Math.max(a, c.a), hi = Math.min(b, c.b);
    if (hi - lo <= 1e-6) continue;
    const s = c.o + lo - c.a, e = c.o + hi - c.a;
    if (out.length && Math.abs(out.at(-1)[1] - s) < 1e-6) out.at(-1)[1] = e;
    else out.push([s, e]);
  }
  return out;
}

/* ---------------- durum yardımcıları ---------------- */
const current = () => state.project && state.project.history.at(-1);
const viewVer = () => {
  if (!state.project) return null;
  return (state.preview && state.project.history.find((v) => v.version === state.preview)) || current();
};
const isPreviewing = () => !!state.project && !!state.preview && state.preview !== current().version;
const canEdit = () => !!state.project && !state.busy && !isPreviewing();

async function work(label, fn) {
  state.busy = true;
  state.working = { label, t0: Date.now() };
  renderAll();
  const tick = setInterval(renderBusy, 1000);
  try { return await fn(); }
  finally {
    clearInterval(tick);
    state.busy = false;
    state.working = null;
    renderAll();
  }
}

/* ---------------- render: ana ---------------- */
function renderAll() {
  renderHeader();
  renderVersionBar();
  renderChat();
  renderQuick();
  renderComposer();
  renderLayers();
  renderBusy();
  syncPlayers();
}

function renderHeader() {
  const p = state.project, v = viewVer();
  $("#uploadCard").hidden = !!p || state.busy;
  $("#newProject").hidden = !p;
  $("#projectChip").textContent = p ? `${p.source_name} · ham ${fmt(p.source_duration)} sn` : "Videonuzu yükleyin, yapay zeka kurgulasın.";
  const pill = $("#versionPill");
  pill.hidden = !p;
  if (p) {
    pill.textContent = `v${v.version} · ${fmt(v.duration)} sn` + (isPreviewing() ? " · önizleme" : "");
    pill.classList.toggle("previewing", isPreviewing());
  }
  const dl = $("#downloadBtn");
  dl.setAttribute("aria-disabled", p ? "false" : "true");
  if (p) { dl.href = v.video_url; dl.setAttribute("download", `reels_${p.project_id}_v${v.version}.mp4`); }
  else { dl.href = "#"; }
}

function renderVersionBar() {
  const p = state.project;
  $("#versionBar").hidden = !p;
  $("#previewBanner").hidden = !isPreviewing();
  if (!p) return;
  const cur = current();
  const undo = $("#undoBtn");
  undo.disabled = state.busy || cur.parent == null;
  undo.title = cur.parent == null ? "Geri dönülecek önceki sürüm yok" : `v${cur.parent} sürümüne döner (yeni sürüm olarak kaydedilir)`;
  const sel = $("#versionSelect");
  sel.replaceChildren(...p.history.map((v) =>
    h("option", { value: v.version }, `v${v.version} · ${fmt(v.duration)} sn${v.version === cur.version ? " (güncel)" : ""}`)));
  sel.value = String((viewVer() || cur).version);
  sel.disabled = state.busy;
  if (isPreviewing()) {
    $("#previewText").textContent = `v${state.preview} önizleniyor (güncel: v${cur.version}).`;
    $("#useVersionBtn").disabled = state.busy;
  }
}

function botBubbleForVersion(v, p) {
  const isFirst = v.version === 1;
  const kids = [];
  if (v.restored_from) {
    kids.push(h("div", { class: "title" }, `v${v.restored_from} sürümüne dönüldü`));
    kids.push(h("div", { class: "summary" }, `Bu içerik yeni sürüm olarak (v${v.version}) kaydedildi.`));
  } else if (isFirst) {
    kids.push(h("div", { class: "title" }, "İlk kurgu hazır"));
    if (v.edl.summary) kids.push(h("div", { class: "summary" }, v.edl.summary));
  } else {
    const manual = !v.instruction;
    kids.push(h("div", { class: "title" }, manual ? "Elle düzenleme uygulandı" : "Değişiklikler uygulandı"));
    kids.push(h("ul", {}, v.changes.map((c) => h("li", {}, c.replace(/^Elle: /, "")))));
  }
  const cls = ["tag"];
  if (v.version === current().version) cls.push("current");
  if (isPreviewing() && v.version === state.preview) cls.push("viewing");
  kids.push(h("button", { class: cls.join(" "), type: "button", title: "Bu sürümü önizle", onclick: () => previewVersion(v.version) },
    `v${v.version} · ${fmt(v.duration)} sn`));
  return h("div", { class: "msg bot" }, kids);
}

function renderChat() {
  const chat = $("#chat");
  const p = state.project;
  const nodes = [];
  if (!p && !state.local.length && !state.working) nodes.push(chatEmptyEl);
  if (p) {
    for (const v of p.history) {
      if (!v.restored_from && (v.instruction || v.version === 1)) {
        nodes.push(h("div", { class: "msg user" }, v.instruction || "Videoyu otomatik kurgula"));
      }
      nodes.push(botBubbleForVersion(v, p));
      for (const m of state.local.filter((x) => x.after === v.version)) nodes.push(localBubble(m));
    }
  } else {
    for (const m of state.local.filter((x) => x.after === 0)) nodes.push(localBubble(m));
  }
  if (state.working) {
    nodes.push(h("div", { class: "msg bot typing", id: "typing" },
      h("div", { class: "spinner sm" }), h("span", { id: "typingText" }, workingText())));
  }
  const atBottom = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 80;
  chat.replaceChildren(...nodes);
  if (atBottom || state.working) chat.scrollTop = chat.scrollHeight;
}
function localBubble(m) {
  return m.role === "user"
    ? h("div", { class: "msg user" }, m.text)
    : h("div", { class: "msg bot" + (m.error ? " error" : "") }, m.error ? h("div", { class: "title" }, "Hata") : null, m.text);
}
const workingText = () => state.working ? `${state.working.label}… (${Math.floor((Date.now() - state.working.t0) / 1000)} sn)` : "";
function renderBusy() {
  const on = !!state.working;
  $("#busy").hidden = !on;
  if (on) $("#busyText").textContent = workingText();
  const t = $("#typingText");
  if (t) t.textContent = workingText();
}

function renderQuick() {
  const box = $("#quick");
  if (!box.childElementCount) {
    for (const [label, text] of QUICK_ACTIONS) {
      box.append(h("button", { class: "chip-btn", type: "button", "aria-label": label, title: text, onclick: () => sendInstruction(text) }, label));
    }
  }
  for (const b of box.children) b.disabled = !canEdit();
}

function renderComposer() {
  const ok = canEdit();
  const input = $("#msgInput");
  input.disabled = !ok;
  $("#sendBtn").disabled = !ok;
  input.placeholder = !state.project ? "Önce bir video yükleyin"
    : isPreviewing() ? "Eski sürüm önizleniyor — önce ‘Bu sürümden devam et’" : "Örn: 3. saniyedeki whoosh’u kaldır";
}

/* ---------------- render: katmanlar ---------------- */
function renderLayers() {
  const box = $("#layers");
  const p = state.project, v = viewVer();
  if (!p) {
    box.replaceChildren(h("h2", {}, "Aktif Kurgu Katmanları"),
      h("p", { class: "muted small" }, "Video yüklenince kesimler, zoom ve ses efektleri burada listelenir."));
    return;
  }
  const edl = v.edl, cuts = buildCuts(edl, p.source_duration);
  const total = v.duration, disabled = !canEdit();
  const pct = (t) => `${Math.min(100, Math.max(0, (t / total) * 100))}%`;

  const zooms = edl.zoom_effects.map((z, i) => ({ z, i, pieces: mapRange(cuts, z.start, z.end) })).filter((x) => x.pieces.length);
  const sfx = edl.sfx_events.map((s, i) => ({ s, i, t: mapPoint(cuts, s.timestamp) })).filter((x) => x.t !== null);
  const bgm = edl.bgm;
  const hasBgm = bgm.track && bgm.track !== "none";

  /* zaman çizelgesi */
  const ruler = h("div", { class: "tl-track" });
  const step = total > 40 ? 10 : total > 16 ? 5 : total > 6 ? 2 : 1;
  for (let t = 0; t <= total + 1e-6; t += step) ruler.append(h("span", { class: "tick", style: `left:${pct(t)}` }, `${t}s`));
  const trackCut = h("div", { class: "tl-track", title: "Kesim noktaları" },
    h("div", { class: "tl-item cut", style: "left:0;right:0" }),
    cuts.slice(1).map((c) => h("div", { class: "tl-item join", style: `left:${pct(c.o)}` })));
  const trackZoom = h("div", { class: "tl-track" }, zooms.flatMap((x) => x.pieces.map(([s, e]) =>
    h("div", { class: "tl-item zoom", style: `left:${pct(s)};width:calc(${pct(e)} - ${pct(s)})`, title: `Zoom ×${x.z.scale}` }))));
  const trackSfx = h("div", { class: "tl-track" }, sfx.map((x) =>
    h("div", { class: "tl-item sfx", style: `left:${pct(x.t)}`, title: `${x.s.type} @ ${fmt(x.t)} sn` })));
  const trackBgm = h("div", { class: "tl-track" }, hasBgm
    ? h("div", { class: "tl-item bgm", style: `opacity:${0.35 + Math.min(bgm.volume / 0.4, 1) * 0.65}` }, `${niceName(bgm.track)} · ${Math.round(bgm.volume * 100)}%`) : null);
  const tracks = [trackCut, trackZoom, trackSfx, trackBgm];
  const seekFromClick = (ev) => {
    const r = ev.currentTarget.getBoundingClientRect();
    seekEdited(((ev.clientX - r.left) / r.width) * total);
  };
  tracks.forEach((t) => t.addEventListener("click", seekFromClick));
  const rows = [["", ruler, "ruler"], ["Kesim", trackCut], ["Zoom", trackZoom], ["SFX", trackSfx], ["Müzik", trackBgm]];
  const playhead = h("div", { class: "playhead", id: "playhead", hidden: true });
  const timeline = h("div", { class: "tl-body" },
    h("div", { class: "tl" }, rows.map(([label, track, cls]) =>
      h("div", { class: "tl-row" + (cls ? " " + cls : "") }, h("span", { class: "tl-label" }, label), track))),
    playhead);

  /* çipler */
  const chip = (cls, text, title, seekTo, onDelete, canDelete = true) => h("span", { class: `lchip ${cls}` },
    h("button", { class: "seek", type: "button", title, onclick: () => seekTo != null && seekEdited(seekTo) }, text),
    h("button", { class: "x", type: "button", "aria-label": "Sil", title: canDelete ? "Sil" : "Son kesim silinemez",
      disabled: disabled || !canDelete, onclick: onDelete }, "×"));

  const cutChips = cuts.map((c, i) => chip("c-cut", `${fmt(c.a)}–${fmt(c.b)} sn`, `Ham video: ${fmt(c.a)}–${fmt(c.b)} sn (kurgulanmış ${fmt(c.o)}–${fmt(c.o + c.b - c.a)} sn)`,
    c.o, () => editEdl((e) => { e.keep_segments = cuts.filter((_, j) => j !== i).map((k) => ({ start: k.a, end: k.b })); }), cuts.length > 1));
  const zoomChips = zooms.map((x) => chip("c-zoom", `${fmt(x.pieces[0][0])}–${fmt(x.pieces.at(-1)[1])} sn · ×${x.z.scale}`,
    `Ham video: ${fmt(x.z.start)}–${fmt(x.z.end)} sn`, x.pieces[0][0], () => editEdl((e) => e.zoom_effects.splice(x.i, 1))));
  const sfxChips = sfx.map((x) => chip("c-sfx", `${niceName(x.s.type)} · ${fmt(x.t)} sn · %${Math.round(x.s.volume * 100)}`,
    `${x.s.type} — ham video: ${fmt(x.s.timestamp)} sn`, x.t, () => editEdl((e) => e.sfx_events.splice(x.i, 1))));

  const group = (color, title, chips, emptyText) => h("div", { class: "layer-group" },
    h("div", { class: "layer-title" }, h("span", { class: "dot", style: `background:${color}` }), title),
    chips.length ? h("div", { class: "chips" }, chips) : h("div", { class: "empty-note" }, emptyText));

  /* müzik denetimi */
  const trackNames = [...new Set([...state.bgm, ...(hasBgm ? [bgm.track] : [])])].sort();
  const sel = h("select", { "aria-label": "Arka plan müziği", disabled },
    h("option", { value: "none" }, "Müzik yok"),
    trackNames.map((n) => h("option", { value: n }, niceName(n))));
  sel.value = hasBgm ? bgm.track : "none";
  sel.addEventListener("change", () => editEdl((e) => {
    e.bgm.track = sel.value;
    if (sel.value === "none") e.bgm.volume = 0;
    else if (!(e.bgm.volume > 0)) e.bgm.volume = 0.15;
    if (!e.bgm.fade_out_last_seconds) e.bgm.fade_out_last_seconds = 2;
  }));
  const out = h("output", {}, hasBgm ? `${Math.round(bgm.volume * 100)}%` : "—");
  const range = h("input", { type: "range", min: "0", max: "0.6", step: "0.01", "aria-label": "Müzik ses seviyesi",
    disabled: disabled || !hasBgm, value: String(Math.min(bgm.volume || 0, 0.6)) });
  range.addEventListener("input", () => (out.textContent = `${Math.round(range.value * 100)}%`));
  range.addEventListener("change", () => editEdl((e) => { e.bgm.volume = Number(range.value); }));
  const rmBgm = h("button", { class: "btn small", type: "button", disabled: disabled || !hasBgm,
    onclick: () => editEdl((e) => { e.bgm.track = "none"; e.bgm.volume = 0; }) }, "Müziği kaldır");

  box.replaceChildren(...[
    h("h2", {}, "Aktif Kurgu Katmanları",
      h("span", { class: "total muted" }, `Kurgulanmış süre ${fmt(total)} sn · ham ${fmt(p.source_duration)} sn`)),
    isPreviewing() && h("div", { class: "readonly-note" }, `v${v.version} önizleniyor: katmanlar salt okunur. Düzenlemek için ‘Bu sürümden devam et’.`),
    timeline,
    group("var(--c-cut)", `Kesimler (korunan ham aralıklar) · ${cuts.length}`, cutChips, "Kesim yok, video bütün."),
    group("var(--c-zoom)", `Zoom anları · ${zoomChips.length}`, zoomChips, "Zoom yok."),
    group("var(--c-sfx)", `SFX anları · ${sfxChips.length}`, sfxChips, "Ses efekti yok."),
    h("div", { class: "layer-group" },
      h("div", { class: "layer-title" }, h("span", { class: "dot", style: "background:var(--c-bgm)" }), "Arka plan müziği"),
      h("div", { class: "music" }, sel, range, out, rmBgm)),
  ].filter(Boolean));
  updatePlayhead();
}

/* ---------------- oynatıcılar ---------------- */
const vidE = () => $("#vidEdited"), vidR = () => $("#vidRaw");
function syncPlayers() {
  const p = state.project, v = viewVer();
  $("#frameEmpty").hidden = !!p;
  const e = vidE(), r = vidR();
  e.hidden = !p || state.tab !== "edited";
  r.hidden = !p || state.tab !== "raw";
  $("#tabEdited").classList.toggle("active", state.tab === "edited");
  $("#tabRaw").classList.toggle("active", state.tab === "raw");
  $("#tabEdited").setAttribute("aria-selected", state.tab === "edited");
  $("#tabRaw").setAttribute("aria-selected", state.tab === "raw");
  if (!p) { e.removeAttribute("src"); r.removeAttribute("src"); delete e.dataset.src; delete r.dataset.src; return; }
  if (e.dataset.src !== v.video_url) {
    const t = e.currentTime || 0, playing = !e.paused && !e.ended;
    e.dataset.src = v.video_url;
    e.src = v.video_url + "#t=0.001";  // ilk kareyi göster
    e.addEventListener("loadedmetadata", () => {
      if (t > 0 && t < e.duration - 0.05) e.currentTime = t;
      if (playing) e.play().catch(() => {});
    }, { once: true });
  }
  const rawUrl = `/api/projects/${p.project_id}/source`;
  if (r.dataset.src !== rawUrl) { r.dataset.src = rawUrl; r.src = rawUrl + "#t=0.001"; }
  if (state.tab === "edited") r.pause(); else e.pause();
}
function setTab(tab) { state.tab = tab; syncPlayers(); }
function seekEdited(t) {
  if (!state.project) return;
  if (state.tab !== "edited") setTab("edited");
  const e = vidE();
  if (Number.isFinite(e.duration)) e.currentTime = Math.min(Math.max(t, 0), e.duration - 0.03);
  else e.addEventListener("loadedmetadata", () => (e.currentTime = t), { once: true });
  updatePlayhead();
}
function updatePlayhead() {
  const ph = $("#playhead"), v = viewVer();
  if (!ph || !v) return;
  const e = vidE();
  ph.hidden = state.tab !== "edited" || !Number.isFinite(e.currentTime);
  const frac = Math.min(Math.max(e.currentTime / (v.duration || 1), 0), 1);
  ph.style.left = `calc(var(--label-w) + 6px + (100% - var(--label-w) - 6px) * ${frac})`;
}
let raf = 0;
function loopPlayhead() {
  updatePlayhead();
  raf = vidE().paused ? 0 : requestAnimationFrame(loopPlayhead);
}

/* ---------------- eylemler ---------------- */
async function loadProject(id) {
  const detail = await api(`/api/projects/${id}`);
  state.project = detail;
  state.preview = null;
  try { localStorage.setItem("reels.lastProject", id); } catch { /* özel pencere */ }
  history.replaceState(null, "", `#p=${id}`);
  renderAll();
}

function previewVersion(n) {
  if (!state.project) return;
  state.preview = n === current().version ? null : n;
  renderAll();
}

async function editEdl(mutate) {
  if (!canEdit()) return;
  const edl = structuredClone(current().edl);
  mutate(edl);
  await work("Yeni sürüm render ediliyor", async () => {
    try {
      const r = await api(`/api/projects/${state.project.project_id}/manual-edl`, { method: "POST", json: edl });
      if (!r.changed) toast(r.message || "Değişiklik yok.");
      await loadProject(state.project.project_id);
    } catch (e) {
      toast(e.message, true);
      await loadProject(state.project.project_id).catch(() => {});
    }
  });
}

async function sendInstruction(text) {
  text = (text || "").trim();
  if (!text || !canEdit()) return;
  const after = current().version;
  const pending = { after, role: "user", text };
  state.local.push(pending);
  $("#msgInput").value = "";
  autoGrow();
  renderChat();
  await work("Kurgu asistanı isteğinizi uyguluyor", async () => {
    try {
      const r = await api(`/api/projects/${state.project.project_id}/edit`, { method: "POST", json: { instruction: text } });
      if (r.changed) state.local = state.local.filter((m) => m !== pending);   // geçmişte zaten var
      else state.local.push({ after, role: "bot", text: r.message });
      await loadProject(state.project.project_id);
    } catch (e) {
      state.local.push({ after, role: "bot", error: true, text: e.message });
    }
  });
}

async function revert(version) {
  if (state.busy || !state.project) return;
  await work("Sürüme dönülüyor", async () => {
    try {
      const r = await api(`/api/projects/${state.project.project_id}/revert`, { method: "POST", json: version ? { version } : {} });
      if (!r.changed) toast(r.message);
      await loadProject(state.project.project_id);
    } catch (e) { toast(e.message, true); }
  });
}

/* ---------------- yükleme ---------------- */
function pickFile(file) {
  if (!file) return;
  const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
  if (!ALLOWED_EXT.includes(ext)) return toast(`Desteklenmeyen dosya: ${ext}. İzinli: ${ALLOWED_EXT.join(" ")}`, true);
  if (file.size > MAX_UPLOAD) return toast("Dosya 500 MB sınırını aşıyor.", true);
  state.file = file;
  const fn = $("#fileName");
  fn.hidden = false;
  fn.textContent = `${file.name} · ${(file.size / 1048576).toFixed(1)} MB`;
  $("#dropzone").classList.add("has-file");
  $("#startBtn").disabled = false;
}

function postCreate(file, instruction, onUploadProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/projects/create");
    xhr.timeout = 15 * 60 * 1000;
    xhr.upload.onprogress = (e) => e.lengthComputable && onUploadProgress(e.loaded / e.total);
    xhr.upload.onload = () => onUploadProgress(1);
    xhr.onload = () => {
      let d = null;
      try { d = JSON.parse(xhr.responseText); } catch { /* boş */ }
      xhr.status >= 200 && xhr.status < 300 ? resolve(d) : reject(new Error(detailText(d, xhr.status)));
    };
    xhr.onerror = () => reject(new Error("Sunucuya bağlanılamadı."));
    xhr.ontimeout = () => reject(new Error("İşlem zaman aşımına uğradı."));
    const fd = new FormData();
    fd.append("video", file);
    fd.append("instruction", instruction);
    xhr.send(fd);
  });
}

async function startProject() {
  if (!state.file || state.busy) return;
  const instruction = $("#firstPrompt").value.trim();
  const file = state.file;
  state.local = [{ after: 0, role: "user", text: instruction || `${file.name} — otomatik kurgu` }];
  $("#uploadCard").hidden = true;
  await work("Video yükleniyor", async () => {
    try {
      const r = await postCreate(file, instruction, (f) => {
        state.working.label = f < 1 ? `Video yükleniyor %${Math.round(f * 100)}` : "Yapay zeka videoyu izliyor, kurguyu planlıyor ve render alıyor";
        renderBusy();
      });
      state.local = [];
      state.file = null;
      await loadProject(r.project_id);
    } catch (e) {
      state.local.push({ after: 0, role: "bot", error: true, text: e.message });
      $("#uploadCard").hidden = false;
    }
  });
}

function resetProject() {
  if (state.busy) return;
  const id = state.project && state.project.project_id;
  if (id && !confirm(`Yeni proje başlatılsın mı?\nMevcut proje kaydedildi; ${location.origin}/#p=${id} adresinden yeniden açabilirsiniz.`)) return;
  state.project = null; state.preview = null; state.local = []; state.file = null; state.tab = "edited";
  try { localStorage.removeItem("reels.lastProject"); } catch { /* yoksay */ }
  history.replaceState(null, "", location.pathname);
  $("#fileName").hidden = true;
  $("#dropzone").classList.remove("has-file");
  $("#startBtn").disabled = true;
  $("#firstPrompt").value = "";
  renderAll();
}

/* ---------------- olaylar ---------------- */
function autoGrow() {
  const t = $("#msgInput");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 110) + "px";
}

function bind() {
  const dz = $("#dropzone"), fi = $("#fileInput");
  dz.addEventListener("click", () => fi.click());
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fi.click(); } });
  fi.addEventListener("change", () => pickFile(fi.files[0]));
  ["dragenter", "dragover"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => dz.addEventListener(t, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));
  // pencerenin başka yerine bırakılan dosya sayfadan ayrılmasın
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => e.preventDefault());

  $("#startBtn").addEventListener("click", startProject);
  $("#newProject").addEventListener("click", resetProject);
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); sendInstruction($("#msgInput").value); });
  $("#msgInput").addEventListener("input", autoGrow);
  $("#msgInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendInstruction(e.target.value); }
  });
  $("#undoBtn").addEventListener("click", () => revert(null));
  $("#versionSelect").addEventListener("change", (e) => previewVersion(Number(e.target.value)));
  $("#useVersionBtn").addEventListener("click", () => revert(state.preview));
  $("#backToCurrentBtn").addEventListener("click", () => previewVersion(current().version));
  $("#tabEdited").addEventListener("click", () => setTab("edited"));
  $("#tabRaw").addEventListener("click", () => setTab("raw"));
  $("#downloadBtn").addEventListener("click", (e) => { if (e.currentTarget.getAttribute("aria-disabled") === "true") e.preventDefault(); });

  const e = vidE();
  e.addEventListener("timeupdate", updatePlayhead);
  e.addEventListener("seeked", updatePlayhead);
  e.addEventListener("loadedmetadata", updatePlayhead);
  e.addEventListener("play", () => { if (!raf) raf = requestAnimationFrame(loopPlayhead); });
}

async function init() {
  bind();
  renderAll();
  try { state.bgm = (await api("/health")).bgm || []; } catch { /* katman listesi yine çalışır */ }
  const fromHash = (location.hash.match(/^#p=([0-9a-f]{12})$/) || [])[1];
  let id = fromHash;
  if (!id) { try { id = localStorage.getItem("reels.lastProject"); } catch { /* yoksay */ } }
  if (id) {
    try { await loadProject(id); }
    catch { try { localStorage.removeItem("reels.lastProject"); } catch { /* yoksay */ } history.replaceState(null, "", location.pathname); }
  }
}
init();
