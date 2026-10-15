"""The DawaiGraph agent: Gemini with function calling over the graph tools.

The model decides which tool to call and how to explain the result, but every fact it
states must come from a tool result: brands from `resolve_brand`, findings from
`check_person`. It never invents interactions, never says "safe", and never tells
anyone to stop a medicine.
"""
import base64
import json
import os
import pathlib
import time
import urllib.error
import urllib.request

from checker import check
from graph_tools import GraphTools
from memory import Memory, doctor_id as make_doctor_id

MODEL = os.environ.get("DAWAI_MODEL", "gemini-3.5-flash")
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ENV_FILES = [
    pathlib.Path(__file__).resolve().parent.parent / ".env",
    pathlib.Path(__file__).resolve().parent.parent.parent / ".llm.env",
]
MAX_STEPS = 8

SYSTEM = """You help a family keep one safe list of everything an elderly parent takes, across doctors and brand names.

Rules:
- Use the tools for every fact. Brand names, salts, interactions, duplicates, class signals and prices come only from tool results. If a tool returns nothing, say you have no data; never fill gaps from your own knowledge.
- When reading a prescription, call resolve_brand for every medicine. If the best match is weak, or two candidates are close, ask the user which one they mean before saving; show the candidates with their salts.
- Save a prescription with add_prescription only after every brand is resolved, then call check_person and explain the result.
- When there are several findings, call risk_hubs and say which medicine is the most connected to risks, so it is raised first.
- Explain findings in plain words for a son or daughter, most important first: same salt twice, Major interactions, unverified class signals, then Moderate ones. For each, say which doctors to ask and show the path from the tool (brand -> salt -> ... -> brand).
- A class signal is unverified: say it is based on similar drugs and is a question for the doctor, not a known interaction.
- "No data" is not "safe": name the medicines without data and suggest asking the doctor or pharmacist.
- Never tell anyone to stop, change or skip a medicine. Never diagnose. Suggest questions for the prescribing doctor.
- Call people by their name from the tools or the screen context, never by an internal id. If the user's words ("Amma", "Appa", a name) may mean a different person than the one selected, ask which person they mean before saving anything.
- Keep answers short. Reply in the language the user writes in (any Indian language: Hindi, Kannada, Tamil, Telugu, Malayalam, Marathi, Bengali, Gujarati, Punjabi, Odia, Urdu, or English, including mixed Hinglish), unless the message names a reply language. Use the user's script too: Hinglish written in Latin letters gets a Latin-letter reply, and never mix scripts of different languages. Keep brand and salt names as they are."""


def _api_key():
    for f in ENV_FILES:
        if f.exists():
            for line in f.read_text(encoding="utf-8-sig").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"'))
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set (see .env.example)")
    return key


TOOLS = [{"functionDeclarations": [
    {"name": "resolve_brand",
     "description": "Find Indian medicine brands matching a name as written on a prescription or strip (typos and missing strengths are fine). Returns candidates with salts, MRP and a match score from 0 to 1.",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "list_people",
     "description": "List the people whose medicines are tracked, with their carers.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_medicines",
     "description": "The medicines a person takes now, with the prescribing doctor and start date.",
     "parameters": {"type": "object", "properties": {"person_id": {"type": "string"}}, "required": ["person_id"]}},
    {"name": "add_prescription",
     "description": "Save a prescription for a person after every brand has been resolved to a brand_id.",
     "parameters": {"type": "object", "properties": {
         "person_id": {"type": "string"},
         "doctor_name": {"type": "string"},
         "speciality": {"type": "string"},
         "items": {"type": "array", "items": {"type": "object", "properties": {
             "brand_id": {"type": "integer"}, "dose": {"type": "string"}}, "required": ["brand_id"]}}},
         "required": ["person_id", "doctor_name", "items"]}},
    {"name": "check_person",
     "description": "Run every graph check over a person's current medicines: same salt twice, known interactions (DDInter), unverified class signals for salts without data, and salts with no data. Findings are remembered; already-known ones are marked.",
     "parameters": {"type": "object", "properties": {"person_id": {"type": "string"}}, "required": ["person_id"]}},
    {"name": "risk_hubs",
     "description": "Rank the salts a person takes by degree centrality in the interaction network (how many Major/Moderate interactions each salt has across all medicines). The top one is the medicine to raise with the doctor first.",
     "parameters": {"type": "object", "properties": {"person_id": {"type": "string"}}, "required": ["person_id"]}},
    {"name": "cheaper_same_salt",
     "description": "Brands with exactly the same salts and strengths at a lower MRP (2022 prices).",
     "parameters": {"type": "object", "properties": {"brand_id": {"type": "integer"}}, "required": ["brand_id"]}},
]}]


# Which Cypher files each tool runs, so every answer can show how it was reached.
CYPHER_OF = {
    "resolve_brand": ["resolve_brand"],
    "check_person": ["duplicate_salts", "known_interactions", "class_signal", "no_data"],
    "risk_hubs": ["risk_hubs"],
    "cheaper_same_salt": ["cheaper_same_salt"],
    "add_prescription": ["memory: MERGE Rx, LISTS, TAKES"],
    "get_medicines": ["memory: active TAKES"],
    "list_people": ["memory: Person, CARES_FOR"],
}


def _summarise(name, result):
    if isinstance(result, list):
        return f"{len(result)} result(s)"
    if isinstance(result, dict) and name == "check_person":
        return f"{len(result['new']) + len(result['already_known'])} finding(s), {len(result['no_data'])} without data"
    if isinstance(result, dict) and "saved" in result:
        return f"saved {result['saved']} medicine(s)"
    return "done"


class Agent:
    def __init__(self, tools: GraphTools, memory: Memory):
        self.tools = tools
        self.memory = memory
        self.key = _api_key()

    def _call_tool(self, name, args):
        if name == "resolve_brand":
            rows = self.tools.resolve_brand(args["text"], 4)
            return [{k: r[k] for k in ("id", "name", "salts", "mrp", "pack", "match")} for r in rows]
        if name == "list_people":
            res = self.tools.g.ro_query(
                "MATCH (p:Person) OPTIONAL MATCH (c:Carer)-[:CARES_FOR]->(p) RETURN p.id, p.name, collect(c.name)")
            return [{"person_id": r[0], "name": r[1], "carers": r[2]} for r in res.result_set]
        if name == "get_medicines":
            return self.memory.active(args["person_id"])
        if name == "add_prescription":
            import datetime
            import uuid
            doctor_id = make_doctor_id(args["doctor_name"])
            self.memory.add_prescription(
                args["person_id"], "rx-" + uuid.uuid4().hex[:8], datetime.date.today().isoformat(),
                {"id": doctor_id, "name": args["doctor_name"], "speciality": args.get("speciality", "")},
                [{"brand_id": int(i["brand_id"]), "dose": i.get("dose", "")} for i in args["items"]],
            )
            return {"saved": len(args["items"])}
        if name == "check_person":
            result = check(self.tools, self.memory, args["person_id"])
            slim = lambda f: {k: f[k] for k in ("type", "level", "summary", "path", "doctors", "status") if k in f}
            return {"new": [slim(f) for f in result["new"]],
                    "already_known": [slim(f) for f in result["already_known"]],
                    "no_data": [{"brand": n["brand_name"], "salt": n["salt"]} for n in result["no_data"]],
                    "medicines": len(result["active"])}
        if name == "risk_hubs":
            ids = list(dict.fromkeys(a["brand_id"] for a in self.memory.active(args["person_id"])))
            return self.tools.risk_hubs(ids)[:5]
        if name == "cheaper_same_salt":
            return self.tools.cheaper_same_salt(int(args["brand_id"]), 3)
        return {"error": f"unknown tool {name}"}

    def _generate(self, contents):
        body = {"systemInstruction": {"parts": [{"text": SYSTEM}]}, "contents": contents, "tools": TOOLS}
        req = urllib.request.Request(
            API.format(model=MODEL), data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "x-goog-api-key": self.key},
        )
        # The model API sometimes drops a connection; retry a few times with a short backoff.
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return json.loads(resp.read())["candidates"][0]["content"]
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                if isinstance(e, urllib.error.HTTPError) and e.code < 500 and e.code != 429:
                    raise
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))

    def run(self, history, message, image=None, image_type="image/jpeg"):
        """history: earlier `contents` turns (kept by the caller). Returns (reply, trace, history)."""
        parts = [{"text": message}]
        if image:
            parts.append({"inlineData": {"mimeType": image_type, "data": base64.b64encode(image).decode()}})
        contents = list(history) + [{"role": "user", "parts": parts}]
        trace = []
        for _ in range(MAX_STEPS):
            content = self._generate(contents)
            contents.append(content)
            calls = [p["functionCall"] for p in content.get("parts", []) if "functionCall" in p]
            if not calls:
                text = "".join(p.get("text", "") for p in content.get("parts", []))
                return text, trace, contents
            responses = []
            for call in calls:
                try:
                    result = self._call_tool(call["name"], call.get("args", {}))
                except Exception as e:  # report tool errors to the model instead of failing the turn
                    result = {"error": str(e)[:300]}
                trace.append({"tool": call["name"], "args": call.get("args", {}), "result": result,
                              "cypher": CYPHER_OF.get(call["name"], []), "summary": _summarise(call["name"], result)})
                response = {"name": call["name"], "response": {"result": result}}
                if "id" in call:
                    response["id"] = call["id"]
                responses.append({"functionResponse": response})
            contents.append({"role": "user", "parts": responses})
        return "I could not finish this request in a few steps; please try a shorter message.", trace, contents
