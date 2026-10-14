"""Can drug-class siblings predict an interaction we have hidden?

For each DDInter drug X that has an ATC class, hide all of X's interactions and
predict X-Y when at least `share` of X's class siblings (same ATC level 4, at
least `min_sib` siblings with data) interact with Y at Major or Moderate.
Compare the predictions with the hidden truth. This is the offline check for
the agent's "class-level signal" before it is ever shown to a user.
"""
import collections
import json
import pathlib
import random

import pandas as pd

RAW = pathlib.Path(__file__).resolve().parent.parent / "data" / "raw"
SERIOUS = {"Major", "Moderate"}

dd = pd.concat(pd.read_csv(RAW / f"ddinter_{c}.csv") for c in "ABDHLPRV")
dd = dd.drop_duplicates(["DDInterID_A", "DDInterID_B"])
edges = collections.defaultdict(dict)
for a, b, lvl in zip(dd.Drug_A.str.lower(), dd.Drug_B.str.lower(), dd.Level):
    edges[a][b] = lvl
    edges[b][a] = lvl

atc = pd.read_csv(RAW / "wikidata_atc_all.csv")
atc["label"] = atc["label"].str.lower()
classes = collections.defaultdict(set)  # drug -> ATC level-4 codes
for label, code in zip(atc.label, atc.atc):
    if label in edges and isinstance(code, str) and len(code) >= 5:
        classes[label].add(code[:5])
members = collections.defaultdict(set)
for d, cs in classes.items():
    for c in cs:
        members[c].add(d)


def predict(x, share, min_sib):
    sibs = set().union(*(members[c] for c in classes[x])) - {x}
    sibs = {s for s in sibs if edges[s]}
    if len(sibs) < min_sib:
        return None
    votes = collections.Counter()
    for s in sibs:
        for y, lvl in edges[s].items():
            if lvl in SERIOUS and y != x:
                votes[y] += 1
    return {y for y, v in votes.items() if v / len(sibs) >= share}


def evaluate(share, min_sib, sample):
    tp = fp = fn = covered = 0
    major_hit = major_total = 0
    for x in sample:
        pred = predict(x, share, min_sib)
        if pred is None:
            continue
        covered += 1
        truth = {y for y, lvl in edges[x].items() if lvl in SERIOUS}
        major = {y for y, lvl in edges[x].items() if lvl == "Major"}
        tp += len(pred & truth)
        fp += len(pred - truth)
        fn += len(truth - pred)
        major_hit += len(pred & major)
        major_total += len(major)
    return {
        "share": share, "min_siblings": min_sib, "drugs_evaluated": covered,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "major_recall": round(major_hit / major_total, 3) if major_total else None,
        "predicted_pairs": tp + fp,
    }


random.seed(7)
pool = sorted(d for d in classes if edges[d])
sample = random.sample(pool, min(400, len(pool)))
print(json.dumps({
    "ddinter_drugs": len(edges),
    "ddinter_drugs_with_atc_class": len(pool),
    "results": [evaluate(s, m, sample) for s in (0.5, 0.67, 0.8) for m in (2, 3)],
}, indent=2))
