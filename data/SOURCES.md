# Data sources (downloaded 15 Oct 2026)

| Data | Source | Licence | Notes |
|---|---|---|---|
| Indian medicine brands and compositions | Kaggle "A-Z Medicine Dataset of India" (shudhanshusingh), https://www.kaggle.com/datasets/shudhanshusingh/az-medicine-dataset-of-india | CC BY-SA 4.0 | 253,973 rows, last updated Nov 2022; prices are 2022 MRPs |
| Drug classes (ATC codes, labels and alternate labels) | Wikidata SPARQL (property P267) | CC0 | 31,228 label-code rows |
| Drug-drug interactions | DDInter, https://ddinter.scbdd.com/download/ (CSV files A, B, D, H, L, P, R, V) | CC BY-NC-SA 4.0 | 160,235 unique pairs over 1,939 drugs; levels Major / Moderate / Minor / Unknown |

Raw files live in data/raw/ and are not committed; run the download steps in the README.
Derived interaction data keeps the CC BY-NC-SA 4.0 licence (ShareAlike) and is for non-commercial use.
