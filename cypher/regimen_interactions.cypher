// Interactions between the salts of one regimen only: anchored on both ends, so it never walks the whole graph.
MATCH (a:Salt)-[i:INTERACTS]->(b:Salt) WHERE a.key IN $keys AND b.key IN $keys AND i.level IN ['Major', 'Moderate', 'Minor']
RETURN a.key AS a, b.key AS b, i.level AS level
