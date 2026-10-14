"""Download ATC codes (labels and alternate labels) from Wikidata (CC0) into data/raw/wikidata_atc_all.csv."""
import pathlib
import urllib.parse
import urllib.request

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "raw" / "wikidata_atc_all.csv"
QUERY = """SELECT ?item ?label ?atc WHERE {
  ?item wdt:P267 ?atc .
  { ?item rdfs:label ?label } UNION { ?item skos:altLabel ?label }
  FILTER(lang(?label) = "en")
}"""

req = urllib.request.Request(
    "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": QUERY}),
    headers={"Accept": "text/csv", "User-Agent": "DawaiGraph/0.1 (https://github.com/Ankith-m1006/dawaigraph)"},
)
OUT.parent.mkdir(parents=True, exist_ok=True)
with urllib.request.urlopen(req, timeout=300) as r:
    OUT.write_bytes(r.read())
print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
