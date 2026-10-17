"""DawaiGraph as an MCP server, so any MCP client (Claude Desktop, Cursor, an agent framework) can use the graph.

    python app/mcp_server.py            # stdio

Read-only on purpose: these tools check medicines against the graph but never write a
family's memory. Every result carries the graph path it came from.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from mcp.server.mcpserver import MCPServer  # noqa: E402

from graph_tools import GraphTools  # noqa: E402

server = MCPServer(
    name="dawaigraph",
    instructions=(
        "Indian medicine safety checks on a FalkorDB graph of brands, salts, DDInter interactions and ATC drug classes. "
        "Resolve brand names first, then check a regimen. Class signals are unverified (based on similar drugs). "
        "No data is not the same as safe. Never advise stopping a medicine; suggest questions for the prescribing doctor."
    ),
)
_tools = None


def tools():
    global _tools
    if _tools is None:
        _tools = GraphTools()
    return _tools


@server.tool()
def resolve_brand(name: str, limit: int = 5) -> list[dict]:
    """Find Indian medicine brands matching a name as written on a prescription or strip (typos and missing strengths are fine)."""
    rows = tools().resolve_brand(name, max(1, min(limit, 10)))
    return [{k: r[k] for k in ("id", "name", "salts", "mrp", "pack", "match")} for r in rows]


@server.tool()
def check_regimen(brand_ids: list[int]) -> dict:
    """Check a set of brand ids (from resolve_brand) for the same salt twice, known DDInter interactions,
    unverified drug-class signals and salts with no data. Read-only: nothing is saved."""
    t = tools()
    ids = list(dict.fromkeys(int(b) for b in brand_ids))
    return {
        "duplicate_salts": t.duplicate_salts(ids),
        "known_interactions": [i for i in t.known_interactions(ids) if i["level"] in ("Major", "Moderate")],
        "class_signals": t.class_signal(ids),
        "no_data": t.no_data(ids),
        "note": "Class signals are unverified; no data is not safe. Ask the prescribing doctor.",
    }


@server.tool()
def cheaper_same_salt(brand_id: int, limit: int = 3) -> list[dict]:
    """Brands with exactly the same salts and strengths at a lower MRP (2022 prices)."""
    return tools().cheaper_same_salt(int(brand_id), max(1, min(limit, 10)))


@server.tool()
def why_connected(brand_ids: list[int], brand_a: int, brand_b: int) -> dict:
    """Shortest paths between two brands of a regimen (breadth-first search over their salts, DDInter interactions
    and class signals). 2 hops = same salt, 3 = salts interact, 4+ = through a drug class or another medicine.
    No path is not the same as safe."""
    ids = list(dict.fromkeys(int(b) for b in brand_ids))
    return tools().connections(ids, int(brand_a), int(brand_b))


if __name__ == "__main__":
    server.run("stdio")
