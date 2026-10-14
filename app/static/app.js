const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
};

const STATUS = { open: "Open", asked_doctor: "Asked the doctor", resolved: "Resolved", not_relevant: "Not relevant" };
const TYPE_LABEL = { DUPLICATE: "Same salt twice", INTERACTION: "Interaction", CLASS_SIGNAL: "Unverified class signal" };

let people = [];
let current = null;
let draft = [];
let cy = null;

async function loadPeople() {
  people = await api("/api/people");
  $("#people").innerHTML = people.map((p) => `<button data-id="${esc(p.id)}">${esc(p.name)}</button>`).join("");
  $("#people").querySelectorAll("button").forEach((b) => (b.onclick = () => select(b.dataset.id)));
  if (people.length) select(people[0].id);
}

async function select(pid) {
  current = people.find((p) => p.id === pid);
  $("#people").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.id === pid));
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
  renderMeds(person.active);
  renderFindings(result, person.history);
  renderGraph(graph);
}

function renderMeds(active) {
  $("#med-count").textContent = `${active.length} medicines from ${new Set(active.map((a) => a.doctor)).size} prescribers`;
  const byDoctor = {};
  active.forEach((a) => (byDoctor[a.doctor] ||= { speciality: a.speciality, meds: [] }).meds.push(a));
  $("#meds").innerHTML = Object.entries(byDoctor).map(([doc, d]) => `
    <div class="doctor">
      <h3>${esc(doc)}${d.speciality ? " · " + esc(d.speciality) : ""}</h3>
      ${d.meds.map((m) => `
        <div class="med">
          <div><b>${esc(m.brand)}</b><div class="salts">since ${esc(m.since)}</div></div>
          <button class="linkbtn" data-cheaper="${m.brand_id}">Cheaper, same salt?</button>
        </div>
        <div id="cheaper-${m.brand_id}"></div>`).join("")}
    </div>`).join("") || `<p class="empty">No medicines yet.</p>`;
  $("#meds").querySelectorAll("[data-cheaper]").forEach((b) => (b.onclick = () => showCheaper(b.dataset.cheaper)));
}

async function showCheaper(id) {
  const box = $(`#cheaper-${id}`);
  const rows = await api(`/api/brand/${id}/cheaper`);
  box.innerHTML = `<div class="cheaper">${rows.length
    ? rows.map((r) => `${esc(r.name)}: ₹${r.mrp} for ${esc(r.pack)} <span class="muted">(now ₹${r.current_mrp} for ${esc(r.current_pack)})</span>`).join("<br>")
      + `<br><span class="muted">Same salts and strengths. 2022 MRPs; ask the doctor or chemist before switching.</span>`
    : "No cheaper brand with exactly the same salts and strengths in the data."}</div>`;
}

function renderFindings(result, history) {
  const trail = Object.fromEntries(history.map((h) => [h.key, h]));
  const all = [...result.new.map((f) => ({ ...f, isNew: true })), ...result.already_known];
  const counts = all.reduce((c, f) => ((c[f.type] = (c[f.type] || 0) + 1), c), {});
  $("#summary").textContent = Object.entries(counts).map(([k, v]) => `${v} ${TYPE_LABEL[k].toLowerCase()}`).join(" · ");
  $("#findings").innerHTML = all.map((f) => {
    const h = trail[f.key] || { status: f.status, updates: [] };
    const updates = (h.updates || []).filter((u) => u[0]);
    const cls = f.type === "INTERACTION" ? f.level : f.type;
    return `
      <div class="finding ${esc(cls)}">
        <span class="tag ${esc(cls)}">${esc(f.type === "INTERACTION" ? f.level + " interaction" : TYPE_LABEL[f.type])}</span>
        ${f.isNew ? `<span class="tag new">New</span>` : ""}
        <p>${esc(f.summary)}</p>
        <div class="path">${f.path.map((p, i) => i % 2 ? `<em>${esc(p)}</em>` : `<span>${esc(p)}</span>`).join("")}</div>
        <div class="ask">Ask: ${f.doctors.map(esc).join(", ")}</div>
        <div class="actions">
          ${Object.entries(STATUS).map(([k, v]) => `<button data-key="${f.key}" data-status="${k}" class="${h.status === k ? "on" : ""}">${v}</button>`).join("")}
        </div>
        ${updates.length ? `<div class="trail">${updates.map((u) => `${esc(u[0])}: ${esc(STATUS[u[1]] || u[1])} on ${esc(u[2].replace("T", " "))}${u[3] ? " (" + esc(u[3]) + ")" : ""}`).join("<br>")}</div>` : ""}
      </div>`;
  }).join("") || `<p class="empty">No duplicate salts or Major/Moderate interactions between the medicines with data.</p>`;
  $("#findings").querySelectorAll("[data-status]").forEach((b) => (b.onclick = () => setStatus(b.dataset.key, b.dataset.status)));
  $("#nodata").innerHTML = result.no_data.length
    ? `<div class="nodata"><b>No data, which is not the same as safe.</b> There is no interaction data, and not enough similar drugs to reason from, for: ${result.no_data.map((n) => `${esc(n.salt)} (${esc(n.brand_name)})`).join(", ")}. Ask the prescribing doctor or a pharmacist about these.</div>`
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
      { selector: "node", style: { label: "data(name)", "font-size": 10, "text-wrap": "wrap", "text-max-width": 90, color: "#16181d", "text-valign": "bottom", "text-margin-y": 4, "text-background-color": "#f6f7f9", "text-background-opacity": 0.85, "text-background-padding": 1 } },
      { selector: 'node[label="Person"]', style: { "background-color": "#0f766e", width: 26, height: 26, "font-weight": 700 } },
      { selector: 'node[label="Brand"]', style: { "background-color": "#16181d", shape: "round-rectangle", width: 16, height: 16 } },
      { selector: 'node[label="Salt"]', style: { "background-color": "#0f766e", width: 14, height: 14 } },
      { selector: "node[?nodata]", style: { "background-color": "#9ca3af" } },
      { selector: 'node[label="DrugClass"]', style: { "background-color": "#7c3aed", shape: "diamond", width: 16, height: 16 } },
      { selector: "edge", style: { width: 1, "line-color": "#d4d6dc", "curve-style": "bezier" } },
      { selector: 'edge[type="INTERACTS"]', style: { width: 2.5, "line-color": "#b45309" } },
      { selector: 'edge[level="Major"]', style: { "line-color": "#c2410c", width: 3.5 } },
      { selector: 'edge[type="IN_CLASS"]', style: { "line-style": "dotted", "line-color": "#7c3aed" } },
      { selector: 'edge[type="SIGNAL"]', style: { "line-style": "dashed", "line-color": "#7c3aed", width: 2.5, label: "data(level)", "font-size": 9, color: "#7c3aed", "text-background-color": "#f6f7f9", "text-background-opacity": 1 } },
    ],
  });
}

let timer = null;
$("#q").addEventListener("input", () => {
  clearTimeout(timer);
  const q = $("#q").value.trim();
  if (q.length < 2) return ($("#suggest").style.display = "none");
  timer = setTimeout(async () => {
    const rows = await api(`/api/search?q=${encodeURIComponent(q)}`);
    $("#suggest").innerHTML = rows.map((r, i) => `<div data-i="${i}"><b>${esc(r.name)}</b><small>${esc(r.salts.join(" + "))} · ₹${r.mrp}</small></div>`).join("")
      || `<div class="empty">Not found in the data. Check the spelling on the strip.</div>`;
    $("#suggest").style.display = "block";
    $("#suggest").querySelectorAll("[data-i]").forEach((d) => (d.onclick = () => {
      draft.push({ ...rows[d.dataset.i], dose: "" });
      $("#q").value = "";
      $("#suggest").style.display = "none";
      renderDraft();
    }));
  }, 220);
});

function renderDraft() {
  $("#draft").innerHTML = draft.map((d, i) => `<li><span><b>${esc(d.name)}</b><br><small class="muted">${esc(d.salts.join(" + "))}</small></span>
    <input placeholder="dose, e.g. 1 at night" data-dose="${i}" value="${esc(d.dose)}"><button class="linkbtn" data-rm="${i}">remove</button></li>`).join("");
  $("#draft").querySelectorAll("[data-dose]").forEach((el) => (el.oninput = () => (draft[el.dataset.dose].dose = el.value)));
  $("#draft").querySelectorAll("[data-rm]").forEach((el) => (el.onclick = () => { draft.splice(el.dataset.rm, 1); renderDraft(); }));
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
};

loadPeople();
