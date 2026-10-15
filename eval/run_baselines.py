"""DawaiGraph vs the same model without the graph, on synthetic regimens of real Indian brands.

Ground truth comes straight from the source tables with pandas (Kaggle compositions +
DDInter pairs), not from the graph, so the graph is checked by an independent path.

    python eval/run_baselines.py --regimens 30

Writes eval/baselines.json. Scored per regimen, then summed:
- duplicate salts: a salt present in two or more brands of the regimen
- interactions: salt pairs from two different brands that DDInter lists as Major or Moderate
- claims outside DDInter: interactions the model states that DDInter does not list for that pair
"""
import argparse
import collections
import json
import pathlib
import random
import sys
import time
import urllib.request

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "app"))
from agent import API, _api_key  # noqa: E402
from graph_tools import GraphTools  # noqa: E402
from normalise import Resolver, parse  # noqa: E402

RAW = ROOT / "data" / "raw"
SERIOUS = {"Major", "Moderate"}
MODEL = "gemini-3.5-flash"


def load_truth_tables():
    brands = pd.read_csv(RAW / "A_Z_medicines_dataset_of_India.csv")
    dd = pd.concat(pd.read_csv(RAW / f"ddinter_{c}.csv") for c in "ABDHLPRV")
    pairs = {}
    for a, b, lvl in zip(dd.Drug_A.str.lower(), dd.Drug_B.str.lower(), dd.Level):
        pairs[frozenset((a, b))] = lvl
    resolver = Resolver(set(dd.Drug_A) | set(dd.Drug_B))
    return brands.set_index("id"), pairs, resolver


def salt_key(resolver, name):
    hit = resolver.resolve(name.strip().lower())
    return (hit or name).strip().lower()


def truth_for(regimen, brands, pairs, resolver):
    salts_of = {}
    for bid in regimen:
        row = brands.loc[bid]
        parts = [parse(row.short_composition1), parse(row.short_composition2)]
        salts_of[bid] = {salt_key(resolver, p[0]) for p in parts if p}
    count = collections.Counter(s for ss in salts_of.values() for s in ss)
    duplicates = {s for s, n in count.items() if n > 1}
    interactions = set()
    ids = list(regimen)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            for x in salts_of[a]:
                for y in salts_of[b]:
                    if x != y and pairs.get(frozenset((x, y))) in SERIOUS:
                        interactions.add(frozenset((x, y)))
    return duplicates, interactions


def graph_answer(tools, regimen):
    dup = {r["salt"].lower() for r in tools.duplicate_salts(regimen)}
    inter = {frozenset((r["salt_a"].lower(), r["salt_b"].lower()))
             for r in tools.known_interactions(regimen) if r["level"] in SERIOUS}
    return dup, inter


PROMPT = """These are Indian medicine brands one patient takes, exactly as written on the strips:
{brands}

Answer from your own knowledge, as JSON only:
{{"salts": {{"<brand>": ["<active ingredient>", ...]}},
  "duplicate_salts": ["<ingredient present in two or more of these brands>"],
  "interactions": [{{"ingredient_a": "...", "ingredient_b": "...", "severity": "Major|Moderate"}}]}}
List only Major or Moderate drug-drug interactions between ingredients of different brands."""


def llm_answer(key, names, resolver):
    body = {"contents": [{"role": "user", "parts": [{"text": PROMPT.format(brands="\n".join(f"- {n}" for n in names))}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    req = urllib.request.Request(API.format(model=MODEL), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                text = json.loads(resp.read())["candidates"][0]["content"]["parts"][0]["text"]
                data = json.loads(text)
                break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
    dup = {salt_key(resolver, s) for s in data.get("duplicate_salts", [])}
    inter = set()
    for i in data.get("interactions", []):
        a, b = salt_key(resolver, i.get("ingredient_a", "")), salt_key(resolver, i.get("ingredient_b", ""))
        if a and b and a != b:
            inter.add(frozenset((a, b)))
    return dup, inter


def score(found, truth):
    tp = len(found & truth)
    return tp, len(found - truth), len(truth - found)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--regimens", type=int, default=30)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    tools = GraphTools()
    brands, pairs, resolver = load_truth_tables()
    key = _api_key()

    # Brands that are in the graph and whose salts DDInter knows, so the truth is defined.
    ids = [r[0] for r in tools.g.ro_query(
        "MATCH (b:Brand)-[:CONTAINS]->(s:Salt) WITH b, collect(s.ddinter) AS d "
        "WHERE all(x IN d WHERE x = true) RETURN b.id LIMIT 20000").result_set]
    rng = random.Random(args.seed)
    totals = {"graph": collections.Counter(), "llm": collections.Counter()}
    rows = []
    for n in range(args.regimens):
        regimen = rng.sample(ids, rng.randint(5, 8))
        # Make duplicates realistic: in half the regimens add a second brand of a salt already there.
        if n % 2 == 0:
            salt = tools.g.ro_query("MATCH (b:Brand {id: $id})-[:CONTAINS]->(s:Salt) RETURN s.key LIMIT 1", {"id": regimen[0]}).result_set[0][0]
            other = tools.g.ro_query(
                "MATCH (s:Salt {key: $k})<-[:CONTAINS]-(b:Brand) WHERE b.id <> $id RETURN b.id LIMIT 1",
                {"k": salt, "id": regimen[0]}).result_set
            if other:
                regimen.append(other[0][0])
        names = [brands.loc[b, "name"] for b in regimen]
        t_dup, t_int = truth_for(regimen, brands, pairs, resolver)
        g_dup, g_int = graph_answer(tools, regimen)
        l_dup, l_int = llm_answer(key, names, resolver)
        for label, dup, inter in (("graph", g_dup, g_int), ("llm", l_dup, l_int)):
            tp, fp, fn = score(dup, t_dup)
            totals[label].update({"dup_tp": tp, "dup_fp": fp, "dup_fn": fn})
            tp, fp, fn = score(inter, t_int)
            outside = sum(1 for p in inter - t_int if pairs.get(p) not in SERIOUS)
            totals[label].update({"int_tp": tp, "int_fp": fp, "int_fn": fn, "outside_ddinter": outside})
        rows.append({"brands": names, "truth_duplicates": sorted(t_dup), "truth_interactions": len(t_int),
                     "graph_interactions": len(g_int), "llm_interactions": len(l_int)})
        print(f"regimen {n + 1}/{args.regimens}: {len(names)} brands, truth {len(t_int)} interactions, "
              f"graph {len(g_int & t_int)}/{len(t_int)}, llm {len(l_int & t_int)}/{len(t_int)} (+{len(l_int - t_int)} extra)")

    def summary(c):
        p = lambda tp, fp: round(tp / (tp + fp), 3) if tp + fp else None
        r = lambda tp, fn: round(tp / (tp + fn), 3) if tp + fn else None
        return {"duplicate_precision": p(c["dup_tp"], c["dup_fp"]), "duplicate_recall": r(c["dup_tp"], c["dup_fn"]),
                "interaction_precision": p(c["int_tp"], c["int_fp"]), "interaction_recall": r(c["int_tp"], c["int_fn"]),
                "claims_outside_ddinter": c["outside_ddinter"], **dict(c)}

    result = {"model": MODEL, "regimens": args.regimens, "seed": args.seed,
              "dawaigraph": summary(totals["graph"]), "llm_only": summary(totals["llm"]), "rows": rows}
    (ROOT / "eval" / "baselines.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("dawaigraph", "llm_only")}, indent=2))


if __name__ == "__main__":
    main()
