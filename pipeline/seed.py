"""Load the medicine graph into FalkorDB.

    python pipeline/seed.py --graph dawai --max-brands 60000

Graph model:
    (:Salt {key, name, ddinter})            one node per active ingredient
    (:Salt)-[:INTERACTS {level, source}]->(:Salt)
    (:Salt)-[:IN_CLASS]->(:DrugClass {code, name})   ATC level 4 (e.g. M01AB)
    (:Brand {id, name, mrp, pack, maker})-[:CONTAINS {strength}]->(:Salt)

The free FalkorDB Cloud tier has 100 MB, so --max-brands loads the best-known
brands first: the source lists brands within each starting letter roughly by
popularity (Dolo 650, Ecosprin 75 and Telma 40 are in the top 3 of their letters).
A full-text index with phonetic matching on Brand.name resolves typed or
misread brand names.

Idempotent: every write is a MERGE on a key, so a rerun only fills gaps.
"""
import argparse
import collections
import pathlib
import sys
import time

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from db import connect  # noqa: E402
from normalise import Resolver, parse  # noqa: E402

RAW = pathlib.Path(__file__).resolve().parent.parent / "data" / "raw"
BATCH = 2000


def batches(rows, size=BATCH):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


def used_mb(db):
    return db.connection.info("memory")["used_memory"] / 1e6


def load_tables():
    brands = pd.read_csv(RAW / "A_Z_medicines_dataset_of_India.csv")
    brands["rank"] = brands.groupby(brands["name"].str[0].str.upper()).cumcount()
    brands = brands[(~brands["Is_discontinued"]) & (brands["type"] == "allopathy")]
    brands = brands.sort_values(["rank", "id"]).drop_duplicates("name")
    dd = pd.concat(pd.read_csv(RAW / f"ddinter_{c}.csv") for c in "ABDHLPRV")
    dd = dd.drop_duplicates(["DDInterID_A", "DDInterID_B"])
    atc = pd.read_csv(RAW / "wikidata_atc_all.csv").dropna()
    return brands, dd, atc


def build(brands, dd, atc):
    resolver = Resolver(set(dd.Drug_A) | set(dd.Drug_B))

    # Salt key = DDInter name when we can map it, else the Indian label name.
    salts = {}
    for n in set(dd.Drug_A) | set(dd.Drug_B):
        salts[n.lower()] = {"key": n.lower(), "name": n, "ddinter": True}

    brand_rows, contains = [], []
    for row in brands.itertuples(index=False):
        parts = [parse(row.short_composition1), parse(row.short_composition2)]
        parts = [p for p in parts if p]
        if not parts:
            continue
        for name, strength in parts:
            hit = resolver.resolve(name)
            key = hit.lower() if hit else name
            if key not in salts:
                salts[key] = {"key": key, "name": name.title(), "ddinter": False}
            contains.append({"b": int(row.id), "s": key, "strength": strength or ""})
        brand_rows.append({
            "id": int(row.id), "name": row.name, "mrp": float(row[2]),
            "pack": row.pack_size_label, "maker": row.manufacturer_name if isinstance(row.manufacturer_name, str) else "",
        })

    # ATC level-4 classes, looked up by every name a salt is known by.
    by_label = collections.defaultdict(set)
    group_name = {}
    for label, code in zip(atc.label.str.lower(), atc.atc):
        if len(code) == 5:
            group_name.setdefault(code, label)
        if len(code) >= 7:
            by_label[label].add(code[:5])
    in_class = []
    for key, s in salts.items():
        for code in by_label.get(key, set()) | by_label.get(s["name"].lower(), set()):
            in_class.append({"s": key, "c": code})
    classes = [{"code": c, "name": group_name.get(c, "")} for c in {r["c"] for r in in_class}]

    interacts = [
        {"a": a.lower(), "b": b.lower(), "level": lvl}
        for a, b, lvl in zip(dd.Drug_A, dd.Drug_B, dd.Level)
    ]
    return list(salts.values()), classes, in_class, interacts, brand_rows, contains


def run(g, query, rows, label):
    start = time.time()
    for chunk in batches(rows):
        g.query(query, {"rows": chunk})
    print(f"  {label}: {len(rows):,} in {time.time() - start:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="dawai")
    ap.add_argument("--max-brands", type=int, default=0, help="0 = all")
    ap.add_argument("--memory-cap-mb", type=float, default=80)
    args = ap.parse_args()

    db = connect()
    g = db.select_graph(args.graph)
    salts, classes, in_class, interacts, brand_rows, contains = build(*load_tables())
    print(f"salts {len(salts):,} classes {len(classes):,} interactions {len(interacts):,} brands {len(brand_rows):,}")

    for q in (
        "CREATE INDEX FOR (s:Salt) ON (s.key)",
        "CREATE INDEX FOR (c:DrugClass) ON (c.code)",
        "CREATE INDEX FOR (b:Brand) ON (b.id)",
    ):
        try:
            g.query(q)
        except Exception as e:  # already exists on a rerun
            if "already" not in str(e).lower():
                raise

    run(g, "UNWIND $rows AS r MERGE (s:Salt {key: r.key}) SET s.name = r.name, s.ddinter = r.ddinter", salts, "salts")
    run(g, "UNWIND $rows AS r MERGE (c:DrugClass {code: r.code}) SET c.name = r.name", classes, "classes")
    run(g, "UNWIND $rows AS r MATCH (s:Salt {key: r.s}), (c:DrugClass {code: r.c}) MERGE (s)-[:IN_CLASS]->(c)", in_class, "salt classes")
    run(g, "UNWIND $rows AS r MATCH (a:Salt {key: r.a}), (b:Salt {key: r.b}) MERGE (a)-[i:INTERACTS]->(b) SET i.level = r.level, i.source = 'DDInter'", interacts, "interactions")
    print(f"  memory after reference data: {used_mb(db):.1f} MB")

    # brand_rows is already in popularity order (see the module docstring).
    if args.max_brands:
        brand_rows = brand_rows[:args.max_brands]
    by_brand = collections.defaultdict(list)
    for c in contains:
        by_brand[c["b"]].append(c)

    loaded = 0
    for chunk in batches(brand_rows):
        g.query(
            "UNWIND $rows AS r MERGE (b:Brand {id: r.id}) SET b.name = r.name, b.mrp = r.mrp, b.pack = r.pack, b.maker = r.maker",
            {"rows": chunk},
        )
        links = [c for b in chunk for c in by_brand[b["id"]]]
        g.query(
            "UNWIND $rows AS r MATCH (b:Brand {id: r.b}), (s:Salt {key: r.s}) MERGE (b)-[c:CONTAINS]->(s) SET c.strength = r.strength",
            {"rows": links},
        )
        loaded += len(chunk)
        mb = used_mb(db)
        if loaded % 20000 == 0:
            print(f"  brands {loaded:,}: {mb:.1f} MB")
        if mb > args.memory_cap_mb:
            print(f"  stopped at {loaded:,} brands: memory {mb:.1f} MB is over the {args.memory_cap_mb} MB cap")
            break
    try:
        g.query("CALL db.idx.fulltext.createNodeIndex({label: 'Brand', language: 'english'}, {field: 'name', phonetic: 'dm:en'})")
    except Exception as e:
        if "already" not in str(e).lower():
            raise
    print(f"done: {loaded:,} brands, {used_mb(db):.1f} MB with the full-text index")


if __name__ == "__main__":
    main()
