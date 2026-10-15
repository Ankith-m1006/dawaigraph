"""HTTP API and web UI.

    uvicorn server:app --app-dir app --port 8000
"""
import base64
import collections
import datetime
import os
import time
import pathlib
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import Agent
from checker import check
from graph_tools import GraphTools
from memory import Memory, doctor_id as make_doctor_id

STATIC = pathlib.Path(__file__).resolve().parent / "static"

app = FastAPI(title="DawaiGraph")
tools = GraphTools()
memory = Memory(tools.g)
memory.ensure_schema()
agent = Agent(tools, memory)
chats = {}  # chat id -> Gemini conversation contents (in memory; the graph keeps the durable state)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def rows(query, params=None):
    res = tools.g.ro_query(query, params or {})
    cols = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
    return [dict(zip(cols, r)) for r in res.result_set]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/search")
def search(q: str):
    return tools.resolve_brand(q, 6)


@app.get("/api/people")
def people():
    return rows(
        "MATCH (p:Person) OPTIONAL MATCH (c:Carer)-[:CARES_FOR]->(p) "
        "RETURN p.id AS id, p.name AS name, collect({id: c.id, name: c.name}) AS carers ORDER BY p.name"
    )


@app.get("/api/person/{pid}")
def person(pid: str):
    if not rows("MATCH (p:Person {id: $pid}) RETURN p.id AS id", {"pid": pid}):
        raise HTTPException(404, "unknown person")
    return {"active": memory.active(pid), "history": memory.history(pid)}


class Item(BaseModel):
    brand_id: int
    dose: str = ""


class Prescription(BaseModel):
    doctor_name: str
    speciality: str = ""
    date: str = ""
    items: list[Item]


@app.post("/api/person/{pid}/rx")
def add_rx(pid: str, rx: Prescription):
    doctor_id = make_doctor_id(rx.doctor_name)
    memory.add_prescription(
        pid, "rx-" + uuid.uuid4().hex[:8], rx.date or datetime.date.today().isoformat(),
        {"id": doctor_id, "name": rx.doctor_name, "speciality": rx.speciality},
        [i.model_dump() for i in rx.items],
    )
    return check(tools, memory, pid)


@app.post("/api/person/{pid}/check")
def run_check(pid: str):
    return check(tools, memory, pid)


class StatusUpdate(BaseModel):
    carer_id: str
    status: str
    note: str = ""


@app.post("/api/person/{pid}/finding/{key}")
def set_status(pid: str, key: str, upd: StatusUpdate):
    if upd.status not in {"open", "asked_doctor", "resolved", "not_relevant"}:
        raise HTTPException(400, "bad status")
    memory.update_finding(pid, key, upd.status, upd.carer_id, upd.note)
    return memory.history(pid)


@app.post("/api/person/{pid}/stop/{brand_id}")
def stop(pid: str, brand_id: int):
    memory.stop(pid, brand_id, datetime.date.today().isoformat())
    return check(tools, memory, pid)


@app.get("/api/person/{pid}/visit")
def visit_sheet(pid: str):
    """Everything to raise at the next appointments, grouped by the doctor to ask (from the remembered findings)."""
    result = check(tools, memory, pid)
    open_findings = [f for f in result["new"] + result["already_known"] if f.get("status", "open") in ("open", "asked_doctor")]
    by_doctor = {}
    for f in open_findings:
        for d in f["doctors"]:
            by_doctor.setdefault(d, []).append({"type": f["type"], "level": f.get("level", ""), "summary": f["summary"], "status": f.get("status", "open")})
    meds = {}
    for a in result["active"]:
        meds.setdefault(a["doctor"], []).append(a["brand"])
    person = rows("MATCH (p:Person {id: $pid}) RETURN p.name AS name", {"pid": pid})
    return {"person": person[0]["name"] if person else pid, "date": datetime.date.today().isoformat(),
            "doctors": [{"doctor": d, "medicines": meds.get(d, []), "questions": q} for d, q in sorted(by_doctor.items())],
            "no_data": result["no_data"]}


DEMO = pathlib.Path(__file__).resolve().parent.parent / "data" / "synthetic" / "families.json"


@app.post("/api/demo/reset")
def reset_demo():
    """Put the three synthetic families back to their starting state (the public demo is shared by every visitor)."""
    if not _allow("demo-reset"):
        raise HTTPException(429, "The demo was reset a moment ago.")
    import json
    for label in ("Finding", "Rx", "Doctor", "Carer", "Person"):
        tools.g.query(f"MATCH (n:{label}) DETACH DELETE n")
    chats.clear()
    for f in json.loads(DEMO.read_text(encoding="utf-8"))["families"]:
        p = f["person"]
        memory.add_person(p["id"], p["name"], p.get("age"), [tuple(c) for c in f["carers"]])
        for rx in f["prescriptions"]:
            items = []
            for name, dose in rx["items"]:
                hit = tools.resolve_brand(name, 1)
                if hit:
                    items.append({"brand_id": hit[0]["id"], "dose": dose})
            memory.add_prescription(p["id"], rx["id"], rx["date"], rx["doctor"], items)
        check(tools, memory, p["id"])
    return {"reset": True}


@app.get("/api/brand/{brand_id}/cheaper")
def cheaper(brand_id: int):
    return tools.cheaper_same_salt(brand_id, 5)


@app.get("/api/person/{pid}/graph")
def graph(pid: str):
    """The person's sub-graph: brands, salts, and the edges that explain each finding."""
    nodes, edges = {}, []
    nodes[pid] = {"id": pid, "label": "Person", "name": pid}
    for r in rows(
        "MATCH (p:Person {id: $pid})-[t:TAKES]->(b:Brand)-[c:CONTAINS]->(s:Salt) WHERE t.until = '' "
        "MATCH (r:Rx {id: t.rx})<-[:WROTE]-(d:Doctor) "
        "RETURN p.name AS person, b.id AS bid, b.name AS brand, s.key AS skey, s.name AS salt, s.ddinter AS has_data, "
        "d.name AS doctor",
        {"pid": pid},
    ):
        nodes[pid]["name"] = r["person"]
        nodes["b%d" % r["bid"]] = {"id": "b%d" % r["bid"], "label": "Brand", "name": r["brand"], "doctor": r["doctor"]}
        nodes["s:" + r["skey"]] = {"id": "s:" + r["skey"], "label": "Salt", "name": r["salt"], "has_data": r["has_data"]}
        edges.append({"from": pid, "to": "b%d" % r["bid"], "type": "TAKES"})
        edges.append({"from": "b%d" % r["bid"], "to": "s:" + r["skey"], "type": "CONTAINS"})
    salt_keys = [n[2:] for n in nodes if n.startswith("s:")]
    for r in rows(
        "MATCH (a:Salt)-[i:INTERACTS]->(b:Salt) WHERE a.key IN $keys AND b.key IN $keys AND i.level IN ['Major', 'Moderate'] "
        "RETURN a.key AS a, b.key AS b, i.level AS level",
        {"keys": salt_keys},
    ):
        edges.append({"from": "s:" + r["a"], "to": "s:" + r["b"], "type": "INTERACTS", "level": r["level"]})
    # Salts without interaction data hang off their drug class, which is what the class signal reasons over.
    no_data = [n[2:] for n, v in nodes.items() if n.startswith("s:") and not v["has_data"]]
    for r in rows(
        "MATCH (s:Salt)-[:IN_CLASS]->(c:DrugClass) WHERE s.key IN $keys "
        "OPTIONAL MATCH (c)<-[:IN_CLASS]-(sib:Salt) WHERE sib.ddinter = true "
        "RETURN s.key AS s, c.code AS code, count(sib) AS siblings",
        {"keys": no_data},
    ):
        nodes["c:" + r["code"]] = {"id": "c:" + r["code"], "label": "DrugClass", "name": f"{r['code']} ({r['siblings']} drugs with data)"}
        edges.append({"from": "s:" + r["s"], "to": "c:" + r["code"], "type": "IN_CLASS"})
    # The class signal itself: a dashed edge from the salt without data to the salt its class siblings interact with.
    ids = [int(n[1:]) for n in nodes if n.startswith("b")]
    for c in tools.class_signal(ids):
        a = next((k for k, v in nodes.items() if k.startswith("s:") and v["name"] == c["salt_without_data"]), None)
        b = next((k for k, v in nodes.items() if k.startswith("s:") and v["name"] == c["salt_b"]), None)
        if a and b:
            edges.append({"from": a, "to": b, "type": "SIGNAL", "level": f"{c['agreeing']}/{c['siblings']}"})
    unique, seen = [], set()
    for e in edges:
        k = (e["from"], e["to"], e["type"])
        if k not in seen:
            seen.add(k)
            unique.append(e)
    return {"nodes": list(nodes.values()), "edges": unique}


class ChatMessage(BaseModel):
    chat_id: str = ""
    person_id: str = ""
    message: str
    language: str = "Auto"
    image_base64: str = ""
    image_type: str = "image/jpeg"


# The agent spends Gemini credits, so a public deployment limits it per client address.
AGENT_LIMIT = int(os.environ.get("AGENT_LIMIT_PER_10MIN", "20"))
_calls = collections.defaultdict(collections.deque)


def _allow(client):
    now = time.time()
    q = _calls[client]
    while q and now - q[0] > 600:
        q.popleft()
    if len(q) >= AGENT_LIMIT:
        return False
    q.append(now)
    return True


@app.post("/api/agent")
def chat(msg: ChatMessage, request: Request):
    client = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    if not _allow(client):
        raise HTTPException(429, "Too many questions in a short time; please wait a few minutes.")
    chat_id = msg.chat_id or uuid.uuid4().hex
    history = chats.get(chat_id, [])
    text = msg.message
    lang_note = ("Reply in the language the user writes in." if msg.language == "Auto"
                 else f"Reply in {msg.language}.")
    if not history and msg.person_id:
        found = rows("MATCH (p:Person {id: $pid}) RETURN p.name AS name", {"pid": msg.person_id})
        name = found[0]["name"] if found else msg.person_id
        text = (f"(The person selected on screen is {name}, person_id {msg.person_id}. Refer to them by that name. "
                f"{lang_note})\n{text}")
    elif msg.language != "Auto":
        text = f"({lang_note})\n{text}"
    image = base64.b64decode(msg.image_base64) if msg.image_base64 else None
    reply, trace, history = agent.run(history, text, image, msg.image_type)
    # Keep whole conversations: trimming could split a function call from its response.
    chats[chat_id] = history
    return {"chat_id": chat_id, "reply": reply, "tools": [{"tool": t["tool"], "args": t["args"]} for t in trace]}
