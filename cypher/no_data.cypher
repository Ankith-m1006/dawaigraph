// Salts in the regimen with no interaction data and no class to reason from: the agent must say "no data", never "safe".
MATCH (b:Brand)-[:CONTAINS]->(s:Salt)
WHERE b.id IN $brand_ids AND s.ddinter = false
OPTIONAL MATCH (s)-[:IN_CLASS]->(c:DrugClass)
RETURN b.id AS brand, b.name AS brand_name, s.name AS salt, collect(c.code) AS classes
