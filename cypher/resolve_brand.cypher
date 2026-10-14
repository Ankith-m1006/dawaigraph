// Brand names as typed or read from a prescription -> candidate Brand nodes.
// The full-text index uses Double Metaphone, so "Telmaa 40" still finds "Telma 40 Tablet".
CALL db.idx.fulltext.queryNodes('Brand', $query) YIELD node, score
OPTIONAL MATCH (node)-[c:CONTAINS]->(s:Salt)
RETURN node.id AS id, node.name AS name, node.mrp AS mrp, node.pack AS pack, score,
       collect(s.name + CASE WHEN c.strength <> '' THEN ' ' + c.strength ELSE '' END) AS salts
ORDER BY score DESC
LIMIT $limit
