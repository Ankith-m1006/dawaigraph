"""Load the synthetic families in data/synthetic/families.json and run the first check for each."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
from checker import check  # noqa: E402
from graph_tools import GraphTools  # noqa: E402
from memory import Memory  # noqa: E402

tools = GraphTools()
memory = Memory(tools.g)
memory.ensure_schema()
families = json.loads((ROOT / "data" / "synthetic" / "families.json").read_text(encoding="utf-8"))["families"]
for f in families:
    p = f["person"]
    memory.add_person(p["id"], p["name"], p.get("age"), [tuple(c) for c in f["carers"]])
    for rx in f["prescriptions"]:
        items = []
        for name, dose in rx["items"]:
            hit = tools.resolve_brand(name, 1)
            if not hit:
                print(f"  not found: {name}")
                continue
            items.append({"brand_id": hit[0]["id"], "dose": dose})
        memory.add_prescription(p["id"], rx["id"], rx["date"], rx["doctor"], items)
    result = check(tools, memory, p["id"])
    print(f"{p['name']}: {len(result['active'])} medicines, {len(result['new']) + len(result['already_known'])} findings, "
          f"{len(result['no_data'])} without data")
