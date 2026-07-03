MATCH (n)
UNWIND labels(n) AS node_label
RETURN node_label, count(n) AS node_count
ORDER BY node_label;

MATCH ()-[r]->()
RETURN type(r) AS relationship_type, count(r) AS relationship_count
ORDER BY relationship_type;

MATCH (n)
WHERE size(labels(n)) = 0
RETURN count(n) AS unlabeled_node_count;

MATCH ()-[r]->()
WHERE type(r) = ''
RETURN count(r) AS empty_relationship_type_count;
