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

MATCH (n:SourceArticle)
WHERE NOT (n)-[:DESCRIBES]->(:CanonicalEntity)
RETURN count(n) AS source_article_without_canonical_entity_count;

MATCH (n:CanonicalEntity)
RETURN count(n) AS canonical_entity_count;

MATCH (:SourceArticle)-[r:DESCRIBES]->(:CanonicalEntity)
RETURN count(r) AS source_article_describes_entity_count;

MATCH (n:Polity)
WHERE NOT n:CanonicalEntity
RETURN count(n) AS polity_without_canonical_entity_label_count;

MATCH (n:Reign)
WHERE NOT (:CanonicalEntity)-[:HELD_REIGN]->(n)
RETURN count(n) AS reign_without_monarch_count;

MATCH (n:Reign)
WHERE NOT (n)-[:OF_POLITY]->(:Polity)
RETURN count(n) AS reign_without_polity_count;

MATCH (n:Reign)
WHERE NOT (:SourceArticle)-[:EVIDENCE_FOR]->(n)
RETURN count(n) AS reign_without_source_evidence_count;

MATCH (n:Reign)-[:OF_POLITY]->(p:Polity)
WITH n, count(DISTINCT p) AS polity_count
WHERE polity_count <> 1
RETURN count(n) AS reign_with_invalid_polity_count;

MATCH (n:Reign)
WHERE n.start_year IS NOT NULL
  AND n.end_year IS NOT NULL
  AND n.start_year > n.end_year
RETURN count(n) AS reign_with_invalid_year_range_count;

MATCH (n:Reign)-[:OF_POLITY]->(p:Polity)
WHERE n.review_status <> 'REVIEW_REQUIRED'
WITH p.polity_id AS polity_id,
     n.succession_order AS succession_order,
     collect(DISTINCT n.anchor_source_eid) AS source_eids
WHERE size(source_eids) > 1
RETURN count(*) AS unreviewed_duplicate_polity_order_count;

MATCH (n:Reign)
RETURN count(n) AS reign_count;

MATCH (n:Polity)
RETURN count(n) AS polity_count;

MATCH (n:RoyalAction)
WHERE NOT (:CanonicalEntity)-[:ASSOCIATED_WITH_ACTION]->(n)
RETURN count(n) AS royal_action_without_monarch_count;

MATCH (n:RoyalAction)
WHERE NOT (n)-[:TARGETS]->(:CanonicalEntity)
RETURN count(n) AS royal_action_without_target_count;

MATCH (n:RoyalAction)
WHERE NOT (n)-[:DURING_REIGN]->(:Reign)
RETURN count(n) AS royal_action_without_reign_count;

MATCH (n:RoyalAction)
WHERE NOT (:SourceArticle)-[:EVIDENCE_FOR]->(n)
RETURN count(n) AS royal_action_without_source_evidence_count;

MATCH (n:RoyalAction)-[:DURING_REIGN]->(r:Reign)
WITH n, count(DISTINCT r) AS reign_count
WHERE reign_count <> 1
RETURN count(n) AS royal_action_with_invalid_reign_count;

MATCH (n:RoyalAction)
WHERE n.start_year IS NOT NULL
  AND n.end_year IS NOT NULL
  AND n.start_year > n.end_year
RETURN count(n) AS royal_action_with_invalid_year_range_count;

MATCH (n:RoyalAction)
RETURN count(n) AS royal_action_count;

MATCH (n:CulturalHeritage)
WHERE NOT n:CanonicalEntity
RETURN count(n) AS cultural_heritage_without_canonical_entity_label_count;

MATCH (n:CulturalHeritage)
WHERE n.entity_type = '개념'
RETURN count(n) AS concept_labeled_as_cultural_heritage_count;

MATCH (n:CulturalHeritage)
WHERE NOT n.heritage_form IN ['PHYSICAL', 'DOCUMENT']
RETURN count(n) AS cultural_heritage_with_invalid_form_count;

MATCH (n:CulturalHeritage)
RETURN n.heritage_kind AS heritage_kind, count(n) AS heritage_count
ORDER BY heritage_count DESC, heritage_kind;

MATCH (n:CulturalHeritage)
RETURN count(n) AS cultural_heritage_count;

MATCH (n:SourceImage)
WHERE NOT n:SourceRecord OR n:CanonicalEntity OR n:CulturalHeritage
RETURN count(n) AS source_image_with_invalid_semantic_label_count;

MATCH (n:SourceImage)-[r:DEPICTS]->(:CanonicalEntity)
WHERE r.evidence_field <> 'title'
RETURN count(r) AS source_image_depicts_without_title_evidence_count;

MATCH (n:SourceImage)-[:DEPICTS]->(target:CanonicalEntity)
WITH n, count(DISTINCT target) AS target_count
WHERE target_count > 1
RETURN count(n) AS source_image_with_multiple_depicts_targets_count;

MATCH (n:SourceImage)
WHERE NOT n.local_file_available IN ['Y', 'N']
RETURN count(n) AS source_image_with_invalid_local_file_status_count;

MATCH (n:SourceImage)
WHERE n.local_file_available = 'N'
  AND trim(coalesce(n.original_url, '')) = ''
  AND trim(coalesce(n.thumbnail_url, '')) = ''
RETURN count(n) AS remote_only_source_image_without_remote_url_count;

MATCH (n:SourceImage)
RETURN count(n) AS source_image_count;

MATCH (:SourceImage)-[r:DEPICTS]->(:CanonicalEntity)
RETURN count(r) AS source_image_depicts_entity_count;

MATCH (n:InscriptionContent)
WHERE NOT n:Term OR n:CulturalHeritage OR n:SourceImage
RETURN count(n) AS inscription_content_with_invalid_semantic_label_count;

MATCH (n:InscriptionContent)
WHERE NOT (n)-[:INSCRIBED_ON]->(:CulturalHeritage)
RETURN count(n) AS inscription_content_without_physical_heritage_count;

MATCH (n:InscriptionContent)-[:INSCRIBED_ON]->(target:CulturalHeritage)
WITH n, count(DISTINCT target) AS target_count
WHERE target_count <> 1
RETURN count(n) AS inscription_content_with_invalid_target_count;

MATCH (n:SourceText)
WHERE NOT n:SourceRecord
   OR NOT (n)-[:PRESENTS_TEXT_OF]->(:InscriptionContent)
   OR n.translated_text IS NULL
   OR trim(n.translated_text) = ''
   OR n.original_text IS NULL
   OR trim(n.original_text) = ''
RETURN count(n) AS source_text_with_invalid_content_or_relation_count;
