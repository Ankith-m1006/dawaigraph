"""Day-1 kill-switch: how many salts in Indian brands map to DDInter?

Reports the match rate over all distinct salts, weighted by how many active
brands use each salt, and for the salts in the 2,000 most common compositions.
"""
import collections
import json
import pathlib

import pandas as pd

from normalise import Resolver, parse

RAW = pathlib.Path(__file__).resolve().parent.parent / "data" / "raw"

brands = pd.read_csv(RAW / "A_Z_medicines_dataset_of_India.csv")
brands = brands[(~brands["Is_discontinued"]) & (brands["type"] == "allopathy")]

dd = pd.concat(pd.read_csv(RAW / f"ddinter_{c}.csv") for c in "ABDHLPRV")
resolver = Resolver(set(dd["Drug_A"]) | set(dd["Drug_B"]))

salt_brands = collections.Counter()
for col in ("short_composition1", "short_composition2"):
    for comp in brands[col]:
        parsed = parse(comp)
        if parsed:
            salt_brands[parsed[0]] += 1

matched = {s: resolver.resolve(s) for s in salt_brands}
hit = [s for s, m in matched.items() if m]
weighted = sum(salt_brands[s] for s in hit) / sum(salt_brands.values())
top = [s for s, _ in salt_brands.most_common(300)]
top_rate = sum(1 for s in top if matched[s]) / len(top)

report = {
    "active_allopathy_brands": int(len(brands)),
    "distinct_salts": len(salt_brands),
    "distinct_salts_matched": len(hit),
    "brand_weighted_match_rate": round(weighted, 4),
    "top300_salts_match_rate": round(top_rate, 4),
    "top_unmatched": [(s, salt_brands[s]) for s in top if not matched[s]][:60],
}
print(json.dumps(report, indent=2, ensure_ascii=False))
