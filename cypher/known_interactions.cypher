// Brand -> Salt -[INTERACTS]- Salt <- Brand across every pair of brands in the regimen.
// The returned path is the explanation shown to the user.
MATCH (b1:Brand)-[:CONTAINS]->(s1:Salt)-[i:INTERACTS]-(s2:Salt)<-[:CONTAINS]-(b2:Brand)
WHERE b1.id IN $brand_ids AND b2.id IN $brand_ids AND b1.id < b2.id AND s1 <> s2
RETURN DISTINCT b1.id AS brand_a, b1.name AS brand_a_name, s1.name AS salt_a,
       i.level AS level, i.source AS source,
       s2.name AS salt_b, b2.name AS brand_b_name, b2.id AS brand_b
ORDER BY CASE i.level WHEN 'Major' THEN 0 WHEN 'Moderate' THEN 1 WHEN 'Minor' THEN 2 ELSE 3 END
