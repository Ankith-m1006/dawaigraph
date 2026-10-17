"""The graph queries the agent can call. Each one runs a file from cypher/ and returns rows as dicts."""
import collections
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

    def risk_hubs(self, brand_ids):
        """Salts in the regimen ranked by degree centrality in the interaction network (Major/Moderate links)."""
        return self.run("risk_hubs", {"brand_ids": brand_ids})

    # ---------- why are these two connected? ----------

    def regimen_graph(self, brand_ids):
        """The part of the graph a regimen touches: its brands, their salts, the interactions between those salts
        and the class signals for salts without data. Small (tens of nodes), so path search on it is instant."""
        nodes, edges = {}, []
        for r in self.run("regimen_salts", {"brand_ids": brand_ids}):
            nodes[f"b{r['bid']}"] = {"label": "Brand", "name": r["brand"]}
            nodes[f"s:{r['skey']}"] = {"label": "Salt", "name": r["salt"]}
            edges.append((f"b{r['bid']}", f"s:{r['skey']}", "CONTAINS", ""))
        keys = [n[2:] for n in nodes if n.startswith("s:")]
        for r in self.run("regimen_interactions", {"keys": keys}):
            edges.append((f"s:{r['a']}", f"s:{r['b']}", "INTERACTS", r["level"]))
        for c in self.class_signal(brand_ids):
            a = next((k for k, v in nodes.items() if v["label"] == "Salt" and v["name"] == c["salt_without_data"]), None)
            b = next((k for k, v in nodes.items() if v["label"] == "Salt" and v["name"] == c["salt_b"]), None)
            if a and b:
                edges.append((a, b, "CLASS SIGNAL", f"unverified, {c['agreeing']}/{c['siblings']} drugs in {c['class_code']}"))
        return nodes, edges

    def connections(self, brand_ids, brand_a, brand_b, max_hops=6, max_paths=4):
        """Every shortest path between two brands in the regimen graph (breadth-first search).

        Hop counts: 2 = the same salt in both, 3 = their salts interact, 4 or more = linked through a drug class
        or through a third medicine the person takes. No path means none of those links exist in the data,
        which is not the same as safe."""
        nodes, edges = self.regimen_graph(list(dict.fromkeys(brand_ids + [brand_a, brand_b])))
        adj = collections.defaultdict(list)
        for a, b, kind, detail in edges:
            adj[a].append((b, kind, detail))
            adj[b].append((a, kind, detail))
        start, goal = f"b{brand_a}", f"b{brand_b}"
        if start not in nodes or goal not in nodes:
            return {"paths": [], "hops": None, "note": "One of the medicines is not in this person's list."}
        # BFS that keeps every parent at the shortest distance, so all shortest paths can be read back.
        dist, parents, frontier = {start: 0}, collections.defaultdict(list), [start]
        while frontier and goal not in dist:
            nxt = []
            for u in frontier:
                if dist[u] >= max_hops:
                    continue
                for v, kind, detail in adj[u]:
                    if v not in dist:
                        dist[v] = dist[u] + 1
                        nxt.append(v)
                    if dist[v] == dist[u] + 1:
                        parents[v].append((u, kind, detail))
            frontier = nxt
        if goal not in dist:
            return {"paths": [], "hops": None,
                    "note": f"No link within {max_hops} hops: no shared salt, no interaction listed in DDInter and no class "
                            "signal connects them, directly or through the person's other medicines. No data is not the same as safe."}
        paths = []

        def walk(v, tail):
            if len(paths) >= max_paths:
                return
            if v == start:
                paths.append([nodes[start]["name"]] + tail)
                return
            for u, kind, detail in parents[v]:
                walk(u, [f"{kind} ({detail})" if detail else kind, nodes[v]["name"]] + tail)

        walk(goal, [])
        # A salt in the middle of a path that neither end contains comes from another medicine the person takes.
        own = {b for a, b, kind, _ in edges if kind == "CONTAINS" and a in (start, goal)}
        brands_of = collections.defaultdict(set)
        for a, b, kind, _ in edges:
            if kind == "CONTAINS":
                brands_of[nodes[b]["name"]].add(nodes[a]["name"])
        own_names = {nodes[k]["name"] for k in own}
        through = sorted({m for path in paths for step in path[2:-2:2] if step not in own_names
                          for m in brands_of.get(step, ())})
        return {"paths": paths, "hops": dist[goal], "through_other_medicines": through,
                "note": "Each path is read from the graph. Every link is a separate pairwise fact; a chain does not "
                        "mean a combined effect. CLASS SIGNAL links are unverified."}
