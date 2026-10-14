// Brands with exactly the same salts at the same strengths and a lower MRP.
MATCH (b:Brand {id: $brand_id})-[c:CONTAINS]->(:Salt)
WITH b, count(c) AS n
MATCH (b)-[c:CONTAINS]->(s:Salt)<-[o:CONTAINS]-(alt:Brand)
WHERE alt <> b AND o.strength = c.strength AND alt.mrp < b.mrp
WITH b, n, alt, count(DISTINCT s) AS shared
WHERE shared = n
MATCH (alt)-[ac:CONTAINS]->(:Salt)
WITH b, n, alt, count(ac) AS alt_n
WHERE alt_n = n
RETURN alt.id AS id, alt.name AS name, alt.mrp AS mrp, alt.pack AS pack, alt.maker AS maker,
       b.mrp AS current_mrp, b.pack AS current_pack
ORDER BY alt.mrp ASC
LIMIT $limit
