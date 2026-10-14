"""The graph queries the agent can call. Each one runs a file from cypher/ and returns rows as dicts."""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
from db import connect  # noqa: E402

CYPHER = {p.stem: p.read_text(encoding="utf-8") for p in (ROOT / "cypher").glob("*.cypher")}

# Thresholds checked offline in pipeline/class_inference_eval.py: at 0.8 and 3, precision 0.87 and recall 0.43 on 272 held-out drugs.
CLASS_MIN_SHARE = 0.8
CLASS_MIN_SIBLINGS = 3


FORMS = {"tablet", "tablets", "capsule", "capsules", "syrup", "injection", "suspension", "drops", "cream", "gel", "ointment", "sr", "er", "xr"}


def _tokens(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


class GraphTools:
    def __init__(self, graph="dawai"):
        self.g = connect().select_graph(graph)

    def run(self, name, params, read_only=True):
        query = self.g.ro_query if read_only else self.g.query
        res = query(CYPHER[name], params)
        cols = [h[1] if isinstance(h, (list, tuple)) else h for h in res.header]
        return [dict(zip(cols, row)) for row in res.result_set]

    def resolve_brand(self, text, limit=5):
        """Full-text candidates, re-ranked so the brand whose name matches the typed tokens most closely comes first."""
        typed = _tokens(text)
        words = sorted(typed, key=len, reverse=True)
        rows, seen = [], set()
        # Exact words, prefixes ("50" -> "50mcg") and any word; the re-rank below decides.
        for q in (text, " ".join(w + "*" for w in words), "|".join(words)):
            if not words:
                break
            for r in self.run("resolve_brand", {"query": q, "limit": 25}):
                if r["id"] not in seen:
                    seen.add(r["id"])
                    rows.append(r)
        for r in rows:
            name = _tokens(r["name"]) - FORMS
            # Labels often drop the strength ("Atorva Tablet" is 10 mg): let the salt strengths match typed numbers.
            strengths = {n for salt in r["salts"] for n in re.findall(r"\d+(?:\.\d+)?", salt)}
            name |= {t for t in typed if t.isdigit() and t in strengths}
            exact = sum(1 for t in typed if t in name or any(n.startswith(t) for n in name if t.isdigit()))
            r["match"] = round(exact / max(len(typed | name), 1), 3)
        rows.sort(key=lambda r: (-r["match"], -r["score"], len(r["name"])))
        return rows[:limit]

    def duplicate_salts(self, brand_ids):
        return self.run("duplicate_salts", {"brand_ids": brand_ids})

    def known_interactions(self, brand_ids):
        return self.run("known_interactions", {"brand_ids": brand_ids})

    def class_signal(self, brand_ids):
        """One signal per (salt without data, other salt): the class with the most siblings agreeing wins."""
        rows = self.run("class_signal", {
            "brand_ids": brand_ids, "min_share": CLASS_MIN_SHARE, "min_siblings": CLASS_MIN_SIBLINGS,
        })
        best = {}
        for r in rows:
            key = (r["brand_a"], r["salt_without_data"], r["brand_b"], r["salt_b"])
            if key not in best or (r["agreeing"], r["siblings"]) > (best[key]["agreeing"], best[key]["siblings"]):
                best[key] = r
        for r in best.values():
            r["agreeing"] = int(r["agreeing"])
        return list(best.values())

    def no_data(self, brand_ids):
        return self.run("no_data", {"brand_ids": brand_ids})

    def cheaper_same_salt(self, brand_id, limit=5):
        return self.run("cheaper_same_salt", {"brand_id": brand_id, "limit": limit})
