const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
};

// ---------- interface language ----------
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
let ui = store.get("dawai.ui") || "en";
const t = (key, vars = {}) => {
  let s = (I18N[ui] && I18N[ui][key]) || I18N.en[key] || key;
  for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, v);
  return s;
};
function applyLanguage() {
  document.documentElement.lang = ui;
  $$("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  $$("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  $$("[data-prompt]").forEach((el) => { el.textContent = t(el.dataset.prompt); });
  $("#ui-lang").value = ui;
  const chatLang = $("#lang");
  if (!chatLang.dataset.touched) chatLang.value = UI_TO_CHAT[ui] || "Auto";
}
const CHAT_LANGS = [["Auto", "Auto"], ["English", "English"], ["Hindi", "हिन्दी"], ["Kannada", "ಕನ್ನಡ"], ["Tamil", "தமிழ்"], ["Telugu", "తెలుగు"],
  ["Malayalam", "മലയാളം"], ["Marathi", "मराठी"], ["Bengali", "বাংলা"], ["Gujarati", "ગુજરાતી"], ["Punjabi", "ਪੰਜਾਬੀ"], ["Odia", "ଓଡ଼ିଆ"], ["Urdu", "اردو"]];
$("#lang").innerHTML = CHAT_LANGS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
$("#lang").onchange = () => { $("#lang").dataset.touched = "1"; };
$("#ui-lang").onchange = () => { ui = $("#ui-lang").value; store.set("dawai.ui", ui); applyLanguage(); if (current) refresh(); };

// ---------- tabs ----------
function showTab(name) {
  $$(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
  $$(".pane").forEach((p) => p.classList.toggle("on", p.id === `pane-${name}`));
  if (name === "graph" && cy) setTimeout(() => { cy.resize(); cy.fit(undefined, 30); }, 50);
}
$$(".tabs button").forEach((b) => (b.onclick = () => showTab(b.dataset.tab)));

const TYPE_KEY = { DUPLICATE: "t_dup", INTERACTION: "t_inter", CLASS_SIGNAL: "t_signal" };
const STATUS = ["open", "asked_doctor", "resolved", "not_relevant"];

let people = [];
let current = null;
let draft = [];
let cy = null;
let chatId = "";
let lastResult = null;

async function loadPeople() {
  people = await api("/api/people");
  const counts = await Promise.all(people.map((p) => api(`/api/person/${p.id}`).then((d) => d.history.filter((h) => h.status === "open").length).catch(() => 0)));
  $("#people").innerHTML = people.map((p, i) => {
    const initials = p.name.split(/\s+/)[0].slice(0, 2);
    return `<button class="person" data-id="${esc(p.id)}"><span class="av">${esc(initials)}</span>${esc(p.name)}${counts[i] ? `<span class="badge">${counts[i]}</span>` : ""}</button>`;
  }).join("");
  $$(".person").forEach((b) => (b.onclick = () => select(b.dataset.id)));
  const remembered = store.get("dawai.person");
  if (people.length) select(people.some((p) => p.id === remembered) ? remembered : people[0].id);
}

async function select(pid) {
  current = people.find((p) => p.id === pid);
  chatId = "";
  store.set("dawai.person", pid);
  $$(".person").forEach((b) => b.classList.toggle("on", b.dataset.id === pid));
  $("#carer").innerHTML = current.carers.filter((c) => c.id).map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  draft = [];
  renderDraft();
  await refresh();
}

async function refresh() {
  const [person, result, graph] = await Promise.all([
    api(`/api/person/${current.id}`),
    api(`/api/person/${current.id}/check`, { method: "POST" }),
    api(`/api/person/${current.id}/graph`),
  ]);
  lastResult = result;
  renderSummary(result);
  renderMeds(person.active);
  renderFindings(result, person.history);
  renderGraph(graph);
}

function renderSummary(result) {
  const all = [...result.new, ...result.already_known];
  const n = (pred) => all.filter(pred).length;
  const tiles = [
    ["dup", n((f) => f.type === "DUPLICATE"), "s_dup"],
    ["inter", n((f) => f.type === "INTERACTION"), "s_inter"],
    ["signal", n((f) => f.type === "CLASS_SIGNAL"), "s_signal"],
    ["nodata", result.no_data.length, "s_nodata"],
  ];
  $("#summary").innerHTML = tiles.map(([cls, count, key]) => `<button class="tile ${cls} ${count ? "" : "zero"}" data-go="alerts"><b>${count}</b><span>${esc(t(key))}</span></button>`).join("");
  $$(".tile").forEach((b) => (b.onclick = () => showTab("alerts")));
}

function renderMeds(active) {
  const byDoctor = {};
  active.forEach((a) => (byDoctor[a.doctor] ||= { speciality: a.speciality, meds: [] }).meds.push(a));
  const doctors = Object.keys(byDoctor).length;
  $("#meds").innerHTML = `<p class="muted small">${esc(t("meds_count", { n: active.length, d: doctors }))}</p>` + (Object.entries(byDoctor).map(([doc, d]) => `
    <div class="doctor">
      <h3>${esc(doc)}${d.speciality ? " · " + esc(d.speciality) : ""}</h3>
      ${d.meds.map((m) => `
        <div class="med">
          <div><b>${esc(m.brand)}</b><div class="muted small">${esc(t("since"))} ${esc(m.since)}</div></div>
          <button class="linkbtn" data-cheaper="${m.brand_id}">${esc(t("cheaper"))}</button>
        </div>
        <div id="cheaper-${m.brand_id}"></div>`).join("")}
    </div>`).join("") || `<p class="empty">—</p>`);
  $$("[data-cheaper]").forEach((b) => (b.onclick = () => showCheaper(b.dataset.cheaper)));
}

async function showCheaper(id) {
  const box = $(`#cheaper-${id}`);
  const rows = await api(`/api/brand/${id}/cheaper`);
  box.innerHTML = `<div class="cheaper">${rows.length
    ? rows.map((r) => `<b>${esc(r.name)}</b>: ₹${r.mrp} · ${esc(r.pack)} <span class="muted">(₹${r.current_mrp} · ${esc(r.current_pack)})</span>`).join("<br>") + `<br><span class="muted small">${esc(t("cheaper_note"))}</span>`
    : esc(t("cheaper_none"))}</div>`;
}

function renderFindings(result, history) {
  const trail = Object.fromEntries(history.map((h) => [h.key, h]));
  const all = [...result.new.map((f) => ({ ...f, isNew: true })), ...result.already_known];
  $("#findings").innerHTML = all.map((f) => {
    const h = trail[f.key] || { status: f.status, updates: [] };
    const updates = (h.updates || []).filter((u) => u[0]);
    const cls = f.type === "INTERACTION" ? f.level : f.type;
    const label = f.type === "INTERACTION" ? `${f.level} · ${t("t_inter")}` : t(TYPE_KEY[f.type]);
    return `
      <article class="finding ${esc(cls)}">
        <span class="tag ${esc(cls)}">${esc(label)}</span>${f.isNew ? `<span class="tag new">${esc(t("new_badge"))}</span>` : ""}
        <p>${esc(f.summary)}</p>
        <details class="why"><summary>${esc(t("why"))}</summary>
          <div class="path">${f.path.map((p, i) => (i % 2 ? `<em>${esc(p)}</em>` : `<span>${esc(p)}</span>`)).join("")}</div>
        </details>
        <div class="ask">${esc(t("ask_doctors"))}: ${f.doctors.map((d) => `<span class="doc">${esc(d)}</span>`).join("")}</div>
        <div class="actions">
          <div class="seg">${STATUS.map((k) => `<button data-key="${f.key}" data-status="${k}" class="${h.status === k ? "on" : ""}">${esc(t("st_" + k))}</button>`).join("")}</div>
          <button class="linkbtn" data-explain="${esc(f.summary)}">${esc(t("explain"))}</button>
        </div>
        ${updates.length ? `<div class="trail">${updates.map((u) => `${esc(u[0])}: ${esc(t("st_" + u[1]))} · ${esc(u[2].replace("T", " "))}`).join("<br>")}</div>` : ""}
      </article>`;
  }).join("") || `<p class="empty">${esc(t("none"))}</p>`;
  $$("[data-status]").forEach((b) => (b.onclick = () => setStatus(b.dataset.key, b.dataset.status)));
  $$("[data-explain]").forEach((b) => (b.onclick = () => {
    showTab("ask");
    send(`Explain this finding simply and tell me what to ask the doctor: ${b.dataset.explain}`);
  }));
  $("#nodata").innerHTML = result.no_data.length
    ? `<div class="nodata"><b>${esc(t("nodata_title"))}</b> ${esc(t("nodata_body"))} ${result.no_data.map((n) => `<b>${esc(n.salt)}</b> (${esc(n.brand_name)})`).join(", ")}. ${esc(t("nodata_tail"))}</div>`
    : "";
}

async function setStatus(key, status) {
  const carer = $("#carer").value;
  if (!carer) return;
  await api(`/api/person/${current.id}/finding/${key}`, { method: "POST", body: JSON.stringify({ carer_id: carer, status }) });
  await refresh();
}

function renderGraph(g) {
  const els = [
    ...g.nodes.map((n) => ({ data: { id: n.id, name: n.name, label: n.label, nodata: n.label === "Salt" && !n.has_data } })),
    ...g.edges.map((e, i) => ({ data: { id: "e" + i, source: e.from, target: e.to, type: e.type, level: e.level || "" } })),
  ];
  if (cy) cy.destroy();
  cy = cytoscape({
    container: $("#graph"),
    elements: els,
    layout: { name: "cose", animate: false, randomize: true, nodeRepulsion: 400000, idealEdgeLength: 90, nodeOverlap: 40, componentSpacing: 120, padding: 30 },
    style: [
      { selector: "node", style: { label: "data(name)", "font-size": 10, "font-family": "Plus Jakarta Sans, sans-serif", "text-wrap": "wrap", "text-max-width": 90, color: "#12211d", "text-valign": "bottom", "text-margin-y": 4, "text-background-color": "#f4f8f6", "text-background-opacity": 0.85, "text-background-padding": 1 } },
      { selector: 'node[label="Person"]', style: { "background-color": "#0e7c66", width: 28, height: 28, "font-weight": 700 } },
      { selector: 'node[label="Brand"]', style: { "background-color": "#12211d", shape: "round-rectangle", width: 16, height: 16 } },
      { selector: 'node[label="Salt"]', style: { "background-color": "#0e7c66", width: 14, height: 14 } },
      { selector: "node[?nodata]", style: { "background-color": "#8a9692" } },
      { selector: 'node[label="DrugClass"]', style: { "background-color": "#6d3fd1", shape: "diamond", width: 16, height: 16 } },
      { selector: "edge", style: { width: 1, "line-color": "#d3ddd9", "curve-style": "bezier" } },
      { selector: 'edge[type="INTERACTS"]', style: { width: 2.5, "line-color": "#b26b00" } },
      { selector: 'edge[level="Major"]', style: { "line-color": "#c2410c", width: 3.5 } },
      { selector: 'edge[type="IN_CLASS"]', style: { "line-style": "dotted", "line-color": "#6d3fd1" } },
      { selector: 'edge[type="SIGNAL"]', style: { "line-style": "dashed", "line-color": "#6d3fd1", width: 2.5, label: "data(level)", "font-size": 9, color: "#6d3fd1", "text-background-color": "#f4f8f6", "text-background-opacity": 1 } },
    ],
  });
}

// ---------- manual prescription entry ----------
let timer = null;
$("#q").addEventListener("input", () => {
  clearTimeout(timer);
  const q = $("#q").value.trim();
  if (q.length < 2) return ($("#suggest").style.display = "none");
  timer = setTimeout(async () => {
    const rows = await api(`/api/search?q=${encodeURIComponent(q)}`);
    $("#suggest").innerHTML = rows.map((r, i) => `<div data-i="${i}"><b>${esc(r.name)}</b><small>${esc(r.salts.join(" + "))} · ₹${r.mrp}</small></div>`).join("")
      || `<div class="empty">${esc(t("not_found"))}</div>`;
    $("#suggest").style.display = "block";
    $$("#suggest [data-i]").forEach((d) => (d.onclick = () => {
      draft.push({ ...rows[d.dataset.i], dose: "" });
      $("#q").value = "";
      $("#suggest").style.display = "none";
      renderDraft();
    }));
  }, 220);
});
function renderDraft() {
  $("#draft").innerHTML = draft.map((d, i) => `<li><span><b>${esc(d.name)}</b><br><small class="muted">${esc(d.salts.join(" + "))}</small></span>
    <input placeholder="1-0-1" data-dose="${i}" value="${esc(d.dose)}"><button class="linkbtn" data-rm="${i}">✕</button></li>`).join("");
  $$("[data-dose]").forEach((el) => (el.oninput = () => (draft[el.dataset.dose].dose = el.value)));
  $$("[data-rm]").forEach((el) => (el.onclick = () => { draft.splice(el.dataset.rm, 1); renderDraft(); }));
  $("#save").disabled = !draft.length || !$("#doctor").value.trim();
}
$("#doctor").addEventListener("input", renderDraft);
$("#save").onclick = async () => {
  $("#save").disabled = true;
  await api(`/api/person/${current.id}/rx`, {
    method: "POST",
    body: JSON.stringify({ doctor_name: $("#doctor").value.trim(), speciality: $("#speciality").value.trim(), items: draft.map((d) => ({ brand_id: d.id, dose: d.dose })) }),
  });
  draft = [];
  $("#doctor").value = "";
  $("#speciality").value = "";
  renderDraft();
  await refresh();
  showTab("alerts");
};

// ---------- chat ----------
let photo = null;
$("#photo").onchange = () => {
  photo = $("#photo").files[0] || null;
  $("#photo-name").textContent = photo ? `📎 ${photo.name}` : "";
};
const bubble = (cls, html) => {
  const hint = $("#chat .hint");
  if (hint) hint.remove();
  const div = document.createElement("div");
  div.className = `bubble ${cls}`;
  div.innerHTML = html;
  $("#chat").appendChild(div);
  $("#chat").scrollTop = $("#chat").scrollHeight;
  return div;
};
const md = (text) => {
  // Small Markdown subset for the agent's replies: headings, rules, bullets, bold, italics and code.
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>").replace(/(^|[^*])\*([^*]+)\*/g, "$1<i>$2</i>");
  const out = [];
  let list = false;
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[*-] (.*)$/);
    if (bullet) { if (!list) { out.push("<ul>"); list = true; } out.push(`<li>${inline(bullet[1])}</li>`); continue; }
    if (list) { out.push("</ul>"); list = false; }
    if (/^-{3,}$/.test(line.trim())) out.push("<hr>");
    else if (/^#{1,6} /.test(line)) out.push(`<h4>${inline(line.replace(/^#+ /, ""))}</h4>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  if (list) out.push("</ul>");
  return out.join("");
};
async function send(preset) {
  const text = (preset ?? $("#ask").value).trim();
  if (!text && !photo) return;
  bubble("me", esc(text || "📷") + (photo ? " 📷" : ""));
  $("#ask").value = "";
  const waiting = bubble("bot", `<span class="muted">${esc(t("thinking"))}</span>`);
  const body = { chat_id: chatId, person_id: current?.id || "", message: text || "Read this prescription.", language: $("#lang").value };
  if (photo) {
    body.image_base64 = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result.split(",")[1]); r.readAsDataURL(photo); });
    body.image_type = photo.type || "image/jpeg";
  }
  photo = null; $("#photo").value = ""; $("#photo-name").textContent = "";
  try {
    const out = await api("/api/agent", { method: "POST", body: JSON.stringify(body) });
    chatId = out.chat_id;
    waiting.innerHTML = md(out.reply) + (out.tools.length ? `<span class="tools">${esc(t("tools"))}: ${out.tools.map((x) => esc(x.tool)).join(" → ")}</span>` : "");
    await refresh();
  } catch (e) {
    waiting.innerHTML = `<span class="muted">${esc(String(e).slice(0, 160))}</span>`;
  }
}
$("#send").onclick = () => send();
$("#ask").addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
$$(".chip").forEach((c) => (c.onclick = () => send(c.textContent)));

// Speech to text with the browser's own engine (Chrome/Android, Edge, Safari on macOS and iOS). Needs HTTPS or localhost.
const SPEECH_LANG = { Auto: { en: "en-IN", hi: "hi-IN", kn: "kn-IN" }, English: "en-IN", Hindi: "hi-IN", Kannada: "kn-IN", Tamil: "ta-IN", Telugu: "te-IN",
  Malayalam: "ml-IN", Marathi: "mr-IN", Bengali: "bn-IN", Gujarati: "gu-IN", Punjabi: "pa-IN", Odia: "or-IN", Urdu: "ur-IN" };
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
if (Recognition && window.isSecureContext) {
  $("#mic").hidden = false;
  let rec = null;
  $("#mic").onclick = () => {
    if (rec) { rec.stop(); return; }
    rec = new Recognition();
    const choice = SPEECH_LANG[$("#lang").value];
    rec.lang = typeof choice === "string" ? choice : choice[ui] || "en-IN";
    rec.interimResults = true;
    rec.onresult = (e) => { $("#ask").value = Array.from(e.results).map((r) => r[0].transcript).join(""); };
    rec.onend = () => { $("#mic").classList.remove("listening"); rec = null; };
    rec.onerror = () => { $("#mic").classList.remove("listening"); rec = null; };
    $("#mic").classList.add("listening");
    rec.start();
  };
}

applyLanguage();
loadPeople();
