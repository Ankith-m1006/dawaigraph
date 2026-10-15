// Degree centrality of each salt in the interaction network: how many Major/Moderate interactions it has.
// For a regimen, the salt with the most is the one to raise with the doctor first.
MATCH (b:Brand)-[:CONTAINS]->(s:Salt)
WHERE b.id IN $brand_ids
MATCH (s)-[i:INTERACTS]-()
WHERE i.level IN ['Major', 'Moderate']
WITH b, s, count(i) AS serious
RETURN b.id AS brand, b.name AS brand_name, s.name AS salt, serious
ORDER BY serious DESC
