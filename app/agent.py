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
import re
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
- When the user asks how or why two medicines are connected, call why_connected and show the path it returns hop by hop. Each link is a separate fact from the data; never say a chain adds up to a combined effect. If it finds no path, say no link is in the data, which is not the same as safe.
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
    {"name": "why_connected",
     "description": "Shortest paths between two of a person's medicines through the graph (breadth-first search over their brands, salts, DDInter interactions and class signals). 2 hops = same salt, 3 = their salts interact, 4+ = linked through a drug class or another medicine the person takes. Medicines are given as written; they are matched to the person's current list.",
     "parameters": {"type": "object", "properties": {
         "person_id": {"type": "string"}, "medicine_a": {"type": "string"}, "medicine_b": {"type": "string"}},
         "required": ["person_id", "medicine_a", "medicine_b"]}},
    {"name": "cheaper_same_salt",
     "description": "Brands with exactly the same salts and strengths at a lower MRP (2022 prices).",
     "parameters": {"type": "object", "properties": {"brand_id": {"type": "integer"}}, "required": ["brand_id"]}},
]}]


# Which Cypher files each tool runs, so every answer can show how it was reached.
CYPHER_OF = {
    "resolve_brand": ["resolve_brand"],
    "check_person": ["duplicate_salts", "known_interactions", "class_signal", "no_data"],
    "risk_hubs": ["risk_hubs"],
    "why_connected": ["regimen_salts", "regimen_interactions", "class_signal", "BFS shortest paths"],
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


# ---------- name check ----------
# Every medicine name in a reply must have come from a tool result (or the user's own words) in this
# conversation. The vocabulary of medicine words is read once from the graph.
_WORD = re.compile(r"[A-Za-z][A-Za-z-]{3,}")
COMMON = set("""about above after again against also another answer asked before being below between both brand
brands called cannot check checked class combination contain contains could daily data days doctor doctors does
dose doses each either even every evening family fever first found from given have having here interaction
interactions issue known like listed medicine medicines might more morning most much must night none only other
please prescribed prescription question questions same should signal since some still such tablet tablets take
takes taking than that their them then there these they this those through times today together under unverified
very what when where which while with within without would your yours path paths hops link links moderate major
minor salt salts strip strips stomach pain heart blood sugar pressure kidney liver bleeding risk risks safe
syrup capsule capsules cream drops injection plus forte active based note using list current start date
speciality general data result results tool tools graph rewrite answer person people carer carers name names
level levels source sources listed pair pairwise direct directly combined effect effects mean means doctor's
discuss discussed suggest suggested question asked ask asking important first second third next last""".split())


def _words(text):
    return {w.lower().strip("-") for w in _WORD.findall(text or "")}


def _strings(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)
    elif value is not None:
        yield str(value)


class Agent:
    def __init__(self, tools: GraphTools, memory: Memory):
        self.tools = tools
        self.memory = memory
        self.key = _api_key()
        self._vocab = None

    def medicine_words(self):
        """(salt words, brand words) in the graph: every word of a salt name and the first word of every brand."""
        if self._vocab is None:
            salts, brands = set(), set()
            for (name,) in self.tools.g.ro_query("MATCH (s:Salt) RETURN s.name").result_set:
                salts |= _words(name)
            for (name,) in self.tools.g.ro_query("MATCH (b:Brand) RETURN b.name").result_set:
                brands |= _words((name or "").split(" ")[0])
            self._vocab = (salts - COMMON, brands - COMMON - salts)
        return self._vocab

    def reply_names(self, reply):
        """Medicine words used in a reply. Salt words count anywhere; a word that is only a brand name counts when
        it is not the first word of a sentence or list item (so "Based on..." is not read as the brand Based)."""
        salts, brands = self.medicine_words()
        found = set()
        for m in _WORD.finditer(reply or ""):
            w = m.group(0).lower().strip("-")
            before = (reply[:m.start()].rstrip(" *_#") or chr(10))[-1]
            sentence_start = before in ".!?:" + chr(10) or reply[:m.start()].strip(" *_#") == ""
            if w in salts or (w in brands and not sentence_start):
                found.add(w)
        return found

    def name_check(self, reply, contents):
        """Medicine words in the reply that no tool returned and the user never wrote."""
        seen = set()
        for turn in contents:
            for part in turn.get("parts", []):
                if "functionResponse" in part:
                    for text in _strings(part["functionResponse"].get("response")):
                        seen |= _words(text)
                elif turn.get("role") == "user" and "text" in part:
                    seen |= _words(part["text"])
        return sorted(self.reply_names(reply) - seen)

    def _person(self, memory, pid):
        if not memory.owns(pid):
            raise ValueError("unknown person_id; call list_people")
        return pid

    def _match_active(self, active, text):
        """Match a medicine as the user wrote it to one of the person's current medicines."""
        want = _words(text) | set(re.findall(r"\d+", text or ""))
        best = []
        for a in active:
            have = _words(a["brand"]) | set(re.findall(r"\d+", a["brand"]))
            score = len(want & have) / max(len(want), 1)
            best.append((score, a))
        best.sort(key=lambda x: -x[0])
        return best[0][1] if best and best[0][0] > 0 else None

    def _call_tool(self, name, args, memory=None):
        memory = memory or self.memory
        if name == "resolve_brand":
            rows = self.tools.resolve_brand(args["text"], 4)
            return [{k: r[k] for k in ("id", "name", "salts", "mrp", "pack", "match")} for r in rows]
        if name == "list_people":
            return [{"person_id": p["id"], "name": p["name"], "carers": [c["name"] for c in p["carers"]]}
                    for p in memory.people()]
        if name == "get_medicines":
            return memory.active(self._person(memory, args["person_id"]))
        if name == "add_prescription":
            import datetime
            import uuid
            doctor_id = make_doctor_id(args["doctor_name"])
            memory.add_prescription(
                self._person(memory, args["person_id"]), "rx-" + uuid.uuid4().hex[:8], datetime.date.today().isoformat(),
                {"id": doctor_id, "name": args["doctor_name"], "speciality": args.get("speciality", "")},
                [{"brand_id": int(i["brand_id"]), "dose": i.get("dose", "")} for i in args["items"]],
            )
            return {"saved": len(args["items"])}
        if name == "check_person":
            result = check(self.tools, memory, self._person(memory, args["person_id"]))
            slim = lambda f: {k: f[k] for k in ("type", "level", "summary", "path", "doctors", "status") if k in f}
            return {"new": [slim(f) for f in result["new"]],
                    "already_known": [slim(f) for f in result["already_known"]],
                    "no_data": [{"brand": n["brand_name"], "salt": n["salt"]} for n in result["no_data"]],
                    "medicines": len(result["active"])}
        if name == "risk_hubs":
            ids = list(dict.fromkeys(a["brand_id"] for a in memory.active(self._person(memory, args["person_id"]))))
            return self.tools.risk_hubs(ids)[:5]
        if name == "why_connected":
            active = memory.active(self._person(memory, args["person_id"]))
            a, b = self._match_active(active, args["medicine_a"]), self._match_active(active, args["medicine_b"])
            if not a or not b or a["brand_id"] == b["brand_id"]:
                return {"error": "could not match both medicines to two different medicines in the current list",
                        "current_list": [x["brand"] for x in active]}
            ids = list(dict.fromkeys(x["brand_id"] for x in active))
            return {"medicine_a": a["brand"], "medicine_b": b["brand"],
                    **self.tools.connections(ids, a["brand_id"], b["brand_id"])}
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

    def run(self, history, message, image=None, image_type="image/jpeg", memory=None):
        """history: earlier `contents` turns (kept by the caller). Returns (reply, trace, history).
        memory: the family's Memory; every person id the model uses must belong to that family."""
        memory = memory or self.memory
        parts = [{"text": message}]
        if image:
            parts.append({"inlineData": {"mimeType": image_type, "data": base64.b64encode(image).decode()}})
        contents = list(history) + [{"role": "user", "parts": parts}]
        trace = []
        retried = False
        for _ in range(MAX_STEPS + 1):
            content = self._generate(contents)
            contents.append(content)
            calls = [p["functionCall"] for p in content.get("parts", []) if "functionCall" in p]
            if not calls:
                text = "".join(p.get("text", "") for p in content.get("parts", []))
                unverified = self.name_check(text, contents)
                if unverified and not retried:
                    # One rewrite: tell the model which names had no source and let it look them up or drop them.
                    retried = True
                    trace.append({"tool": "name_check", "args": {}, "result": unverified, "cypher": [],
                                  "summary": "not from the graph: " + ", ".join(unverified) + " (asked to rewrite)"})
                    contents.append({"role": "user", "parts": [{"text":
                        "Name check: your answer mentions " + ", ".join(unverified) + ", which no tool returned in this "
                        "conversation. Rewrite the answer using only names from tool results. If you need one of them, "
                        "call resolve_brand first; otherwise leave it out."}]})
                    continue
                if unverified:
                    text += "\n\n(Not checked against the medicine data: " + ", ".join(unverified) + ". Treat these names with care.)"
                trace.append({"tool": "name_check", "args": {}, "result": unverified, "cypher": [],
                              "summary": ("all medicine names came from the graph" if not unverified
                                          else "flagged: " + ", ".join(unverified))})
                return text, trace, contents
            responses = []
            for call in calls:
                try:
                    result = self._call_tool(call["name"], call.get("args", {}), memory)
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
