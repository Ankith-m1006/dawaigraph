// The brands of a regimen and the salts they contain (the start of the regimen graph used by path search).
MATCH (b:Brand)-[:CONTAINS]->(s:Salt) WHERE b.id IN $brand_ids
RETURN b.id AS bid, b.name AS brand, s.key AS skey, s.name AS salt
