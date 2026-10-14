// Gap filling for salts that DDInter has no data on at all (often India-only drugs such as aceclofenac).
// For salt x without data and salt y in the regimen: look at x's ATC class siblings that DO have data.
// If at least $min_share of at least $min_siblings siblings interact with y (Major/Moderate),
// raise a class-level signal. It is labelled unverified and never shown as a known interaction.
MATCH (b1:Brand)-[:CONTAINS]->(x:Salt), (b2:Brand)-[:CONTAINS]->(y:Salt)
WHERE b1.id IN $brand_ids AND b2.id IN $brand_ids AND b1.id <> b2.id
  AND x <> y AND x.ddinter = false
MATCH (x)-[:IN_CLASS]->(c:DrugClass)<-[:IN_CLASS]-(sib:Salt)
WHERE sib <> x AND sib.ddinter = true AND sib <> y
OPTIONAL MATCH (sib)-[i:INTERACTS]-(y)
WHERE i.level IN ['Major', 'Moderate']
WITH b1, b2, x, y, c, sib, count(i) > 0 AS hit
WITH b1, b2, x, y, c, count(sib) AS siblings,
     sum(CASE WHEN hit THEN 1 ELSE 0 END) AS agreeing,
     collect(CASE WHEN hit THEN sib.name END) AS examples
WHERE siblings >= $min_siblings AND toFloat(agreeing) / siblings >= $min_share
RETURN b1.id AS brand_a, b1.name AS brand_a_name, x.name AS salt_without_data,
       c.code AS class_code, c.name AS class_name, siblings, agreeing, examples[..4] AS example_siblings,
       y.name AS salt_b, b2.name AS brand_b_name, b2.id AS brand_b
ORDER BY toFloat(agreeing) / siblings DESC
