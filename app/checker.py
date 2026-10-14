"""Run every graph check over a person's active medicines and remember the findings.

This is deterministic: the agent decides when to call it and how to explain the
result, but the findings themselves come only from graph paths.
"""
from graph_tools import GraphTools
from memory import Memory, finding_key

# Most actionable first: same salt twice, Major, unverified class signals, then Moderate.
TYPE_ORDER = {"DUPLICATE": 0, "Major": 1, "CLASS_SIGNAL": 2, "Moderate": 3}


def check(tools: GraphTools, memory: Memory, pid, include_minor=False):
    active = memory.active(pid)
    ids = [a["brand_id"] for a in active]
    doctor_of = {a["brand_id"]: a["doctor"] for a in active}
    findings = []

    for d in tools.duplicate_salts(ids):
        findings.append({
            "key": finding_key("DUPLICATE", d["salt"], d["brand_a"], d["brand_b"]),
            "type": "DUPLICATE", "level": "",
            "summary": f"{d['brand_a_name']} and {d['brand_b_name']} both contain {d['salt']} "
                       f"({d['strength_a'] or '?'} + {d['strength_b'] or '?'}).",
            "brand_ids": [d["brand_a"], d["brand_b"]],
            "path": [d["brand_a_name"], "CONTAINS", d["salt"], "CONTAINS", d["brand_b_name"]],
            "doctors": sorted({doctor_of[d["brand_a"]], doctor_of[d["brand_b"]]}),
        })

    for i in tools.known_interactions(ids):
        if i["level"] in ("Minor", "Unknown") and not include_minor:
            continue
        findings.append({
            "key": finding_key("INTERACTION", i["salt_a"], i["salt_b"], i["brand_a"], i["brand_b"]),
            "type": "INTERACTION", "level": i["level"],
            "summary": f"{i['salt_a']} ({i['brand_a_name']}) and {i['salt_b']} ({i['brand_b_name']}): "
                       f"{i['level']} interaction listed in {i['source']}.",
            "brand_ids": [i["brand_a"], i["brand_b"]],
            "path": [i["brand_a_name"], "CONTAINS", i["salt_a"], f"INTERACTS ({i['level']})", i["salt_b"], "CONTAINS", i["brand_b_name"]],
            "doctors": sorted({doctor_of[i["brand_a"]], doctor_of[i["brand_b"]]}),
        })

    signals = tools.class_signal(ids)
    for c in signals:
        findings.append({
            "key": finding_key("CLASS_SIGNAL", c["salt_without_data"], c["salt_b"], c["brand_a"], c["brand_b"]),
            "type": "CLASS_SIGNAL", "level": "class",
            "summary": f"No interaction data exists for {c['salt_without_data']} ({c['brand_a_name']}). "
                       f"{c['agreeing']} of {c['siblings']} drugs in its class ({c['class_code']}, e.g. "
                       f"{', '.join(c['example_siblings'][:3])}) have a Major or Moderate interaction with "
                       f"{c['salt_b']} ({c['brand_b_name']}). Unverified: a signal to ask about, not a known interaction.",
            "brand_ids": [c["brand_a"], c["brand_b"]],
            "path": [c["brand_a_name"], "CONTAINS", c["salt_without_data"], "IN_CLASS", c["class_code"], "IN_CLASS",
                     f"{c['agreeing']}/{c['siblings']} siblings", "INTERACTS", c["salt_b"], "CONTAINS", c["brand_b_name"]],
            "doctors": sorted({doctor_of[c["brand_a"]], doctor_of[c["brand_b"]]}),
        })

    signalled = {c["salt_without_data"] for c in signals}
    no_data = [n for n in tools.no_data(ids) if n["salt"] not in signalled]

    findings.sort(key=lambda f: TYPE_ORDER.get(f["level"] if f["type"] == "INTERACTION" else f["type"], 9))
    new, known = memory.record(pid, findings)
    return {"active": active, "new": new, "already_known": known, "no_data": no_data}
