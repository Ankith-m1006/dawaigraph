# DawaiGraph

**One list of everything an elderly parent takes, across every doctor and every brand name, checked on a FalkorDB graph.**

Built for Graph Hacks (WeMakeDevs x FalkorDB, 15-18 Oct 2026). Tracks: **Agents That Act on Connected Data** and **Agent Memory and Coordination**.

![DawaiGraph](docs/v2-desktop-alerts.png)

| Ask (phone) | Alerts in Kannada |
|---|---|
| ![Ask](docs/v2-mobile-ask.png) | ![Kannada](docs/v2-mobile-kn-alerts.png) |

## The problem

In India an elderly parent often sees three or four doctors (a GP, a cardiologist, an orthopaedic surgeon, a dentist) plus the chemist. Each one prescribes by **brand name**, and nobody holds the full list. Two failures follow:

1. **The same salt twice.** Aceclo Plus (for knee pain) and Dolo 650 (for fever) both contain paracetamol. The names share nothing.
2. **Interactions across doctors.** The dentist's painkiller meets the cardiologist's warfarin.

There is a third, quieter failure. The open interaction data the world uses (DDInter) is built around drugs sold in the US and Europe. Many of the salts Indians take every day, such as **aceclofenac, nimesulide, etoricoxib, gliclazide, vildagliptin and domperidone**, have **no interaction data at all**. A plain lookup returns nothing, and "nothing found" looks like "safe".

## Why a graph

- Brands carry no meaning. "Dolo 650" and "Aceclo Plus" are unrelated strings that share a salt: a two-hop pattern `Brand-[:CONTAINS]->Salt<-[:CONTAINS]-Brand`. Vector similarity cannot find that.
- Interactions are between **salts**, people hold **brands**, and combination pills hold several salts. "Do any two of these eight brands interact?" is one pattern, `Brand->Salt-[:INTERACTS]-Salt<-Brand`, and the path it returns is the explanation.
- **Filling the data gap is graph reasoning.** For a salt with no data, DawaiGraph walks to its drug class and looks at the class siblings that do have data: `Brand->Salt-[:IN_CLASS]->DrugClass<-[:IN_CLASS]-Sibling-[:INTERACTS]-Salt<-Brand`. If at least 80% of at least 3 siblings interact with a medicine in the regimen, it raises an **unverified class signal**, clearly labelled, never presented as a known interaction.
- Memory is part of the same graph: who takes what since when, which doctor wrote it, which findings were already raised, and which family member followed up.

Take FalkorDB out and nothing is left: the checks are graph traversals.

## Graph model

```
(:Brand {id, name, mrp, pack, maker})-[:CONTAINS {strength}]->(:Salt {key, name, ddinter})
(:Salt)-[:INTERACTS {level, source}]->(:Salt)                 level: Major | Moderate | Minor | Unknown
(:Salt)-[:IN_CLASS]->(:DrugClass {code})                       ATC level 4, e.g. M01AB

(:Person {id, name, age})-[:HAS_RX]->(:Rx {id, date})<-[:WROTE]-(:Doctor {id, name, speciality})
(:Rx)-[:LISTS {dose}]->(:Brand)
(:Person)-[:TAKES {since, until, rx}]->(:Brand)                until = '' while active
(:Person)-[:HAS_FINDING]->(:Finding {key, type, level, summary, status, raised, updated})-[:ABOUT]->(:Brand)
(:Carer {id, name})-[:CARES_FOR]->(:Person)
(:Carer)-[:UPDATED {at, status, note}]->(:Finding)
```

Indexes: range indexes on `Salt.key`, `Brand.id`, `DrugClass.code`, `Person.id`, `Finding.key` and the memory keys; a **full-text index with Double Metaphone phonetic matching** on `Brand.name`, so a misread "Telmaa 40" still finds "Telma 40 Tablet".

A finding's key is a hash of what it is about, so the same problem found in a later session updates the existing node (and keeps its status, such as "asked the doctor") instead of being raised again.

## The Cypher the product depends on

All in [`cypher/`](cypher):

| Query | What it does |
|---|---|
| [`resolve_brand`](cypher/resolve_brand.cypher) | Phonetic full-text search from typed or read brand names to Brand nodes with their salts |
| [`duplicate_salts`](cypher/duplicate_salts.cypher) | Two brands in the regimen with the same salt |
| [`known_interactions`](cypher/known_interactions.cypher) | Every Brand-Salt-INTERACTS-Salt-Brand path across the regimen |
| [`class_signal`](cypher/class_signal.cypher) | The gap-filling traversal through drug-class siblings for salts with no data |
| [`no_data`](cypher/no_data.cypher) | Salts with no data and nothing to reason from, so the app says "no data", never "safe" |
| [`cheaper_same_salt`](cypher/cheaper_same_salt.cypher) | Brands with exactly the same salts and strengths at a lower MRP |

The memory writes are in [`app/memory.py`](app/memory.py).

## Does the class signal work? (measured, not assumed)

[`pipeline/class_inference_eval.py`](pipeline/class_inference_eval.py) uses the same rule as the live query. For 400 randomly sampled DDInter drugs it **hides all of the drug's interactions**, predicts them from its class siblings, and compares with the hidden truth (Major and Moderate).

| Rule (share of siblings, minimum siblings) | Drugs with enough siblings | Precision | Recall | Major recall |
|---|---|---|---|---|
| 50%, 3 | 272 | 0.705 | 0.625 | 0.654 |
| 67%, 3 | 272 | 0.829 | 0.479 | 0.497 |
| **80%, 3 (used in the app)** | **272** | **0.874** | **0.430** | **0.441** |
| 90%, 3 | 272 | 0.894 | 0.352 | 0.363 |

So when DawaiGraph raises a class signal, 87% of the time the held-out data confirms a real Major or Moderate interaction. It finds 43% of them, where a plain lookup finds 0% for a salt with no data. A "false positive" here only means DDInter lists nothing for that pair, which is not proof that there is no interaction. Full output: [`eval/class_inference.json`](eval/class_inference.json).

Salt coverage of the Indian data: 76.7% of active brand-salt uses map to a DDInter drug ([`eval/match_rate.json`](eval/match_rate.json)); most of the rest are the India-specific salts described above.

## Graph vs the same model without the graph

[`eval/run_baselines.py`](eval/run_baselines.py) builds 30 regimens of 5 to 9 real Indian brands (half with a deliberately repeated salt) and asks two systems for the duplicate salts and the Major/Moderate interactions between brands: DawaiGraph's Cypher queries, and **Gemini 3.5 Flash given only the brand names** (temperature 0, JSON answer). The ground truth is computed straight from the source tables with pandas, not from the graph.

| 30 regimens, 119 known interactions | DawaiGraph | Gemini 3.5 Flash alone |
|---|---|---|
| Interactions found (recall) | **119 / 119** | 48 / 119 (40%) |
| Interactions stated that are listed in DDInter (precision) | **100%** | 46% |
| Interactions stated that DDInter does not list | **0** | 52 |
| Duplicate salts found | **24 / 24** | 21 / 24, plus 7 that are not duplicates |

Read this honestly: DawaiGraph scores 100% because the graph is built from the same tables, so this run checks that the queries return exactly what the data says. The point is the right-hand column. The same model that writes DawaiGraph's explanations, asked without the graph, misses 60% of the listed interactions and adds 52 that DDInter does not list (some may be real interactions DDInter lacks, which is why DawaiGraph shows its source for every finding). Full output: [`eval/baselines.json`](eval/baselines.json).

## Use it from any AI assistant (MCP)

[`app/mcp_server.py`](app/mcp_server.py) exposes the graph as an MCP server, read-only on purpose (it checks medicines but never writes a family's memory):

| Tool | What it does |
|---|---|
| `resolve_brand` | Brand name as written on a strip (typos and missing strengths are fine) to brand ids with salts |
| `check_regimen` | Same salt twice, DDInter interactions, unverified class signals and salts with no data, each with its graph path |
| `cheaper_same_salt` | Same salts and strengths at a lower MRP |

Claude Desktop or any MCP client:

```json
{"mcpServers": {"dawaigraph": {"command": "<repo>/.venv/Scripts/python.exe", "args": ["<repo>/app/mcp_server.py"]}}}
```

## Run it

Requirements: Python 3.11+, a FalkorDB instance (FalkorDB Cloud free tier, or `docker run -p 6379:6379 falkordb/falkordb`).

```bash
pip install -r requirements.txt
cp .env.example .env           # FALKORDB_HOST, FALKORDB_PORT, FALKORDB_USERNAME, FALKORDB_PASSWORD

# data (public): see data/SOURCES.md
kaggle datasets download -d shudhanshusingh/az-medicine-dataset-of-india -p data/raw --unzip
for c in A B D H L P R V; do curl -o data/raw/ddinter_$c.csv https://ddinter.scbdd.com/static/media/download/ddinter_downloads_code_$c.csv; done
python pipeline/fetch_atc.py   # drug classes from Wikidata

python pipeline/seed.py --max-brands 70000    # 70k fits the 100 MB free tier; omit the flag for all 246k
python pipeline/load_families.py              # three synthetic families
uvicorn server:app --app-dir app --port 8000
```

## Limits (read these)

- **Not medical advice.** DawaiGraph never says "stop taking" anything; it lists questions for the doctor who prescribed the medicine.
- **No data is not safe.** Salts without data and without enough class siblings are listed as "no data".
- **Class signals are unverified.** They are hypotheses with their evidence shown, not interactions.
- **The brand data is from Nov 2022** (prices are 2022 MRPs) and keeps **at most two salts per brand**, so a third salt in combinations such as Zerodol-SP (serratiopeptidase) is missing.
- The free FalkorDB Cloud tier (100 MB) holds 70,000 of 246,068 active brands, loaded in the order the source lists them within each letter, which puts well-known brands first (Dolo 650, Ecosprin 75 and Telma 40 are in the top three of their letters).
- All people and prescriptions are synthetic.

## Data and licences

| Data | Licence |
|---|---|
| A-Z Medicine Dataset of India (Kaggle, shudhanshusingh) | CC BY-SA 4.0 |
| DDInter drug-drug interactions | CC BY-NC-SA 4.0 (non-commercial; derived interaction data keeps this licence) |
| Wikidata ATC codes | CC0 |

Code: MIT.

## AI assistance (disclosure required by the rules)

Claude (Anthropic) was used heavily as a coding assistant during the event: for research, the data pipeline, the Cypher queries, the evaluation, the web UI and this README. Commits it helped write carry a `Co-Authored-By` line.

<!-- TODO Ankith: before submitting, describe your own part here in your own words (what you decided, checked and tested). -->
