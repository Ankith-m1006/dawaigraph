// Two different brands in the regimen that carry the same salt (e.g. Dolo 650 and a cold tablet that also has paracetamol).
MATCH (b1:Brand)-[c1:CONTAINS]->(s:Salt)<-[c2:CONTAINS]-(b2:Brand)
WHERE b1.id IN $brand_ids AND b2.id IN $brand_ids AND b1.id < b2.id
RETURN s.name AS salt, b1.id AS brand_a, b1.name AS brand_a_name, c1.strength AS strength_a,
       b2.id AS brand_b, b2.name AS brand_b_name, c2.strength AS strength_b
