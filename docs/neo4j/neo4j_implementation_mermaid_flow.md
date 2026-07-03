# Neo4j 구현 Mermaid 전체 구조

이 문서는 Neo4j 구현 흐름을 Mermaid 구조도로만 따라갈 수 있게 정리한 문서다.

포함 범위:

- raw CSV 입력
- 전처리 runner와 4개 스크립트
- seed CSV 7개
- normalized CSV 4개
- dictionary CSV 10개
- mapping CSV 6개
- staging CSV 3개
- 최종 node CSV 14개
- 최종 relationship CSV 27개와 optional relation CSV 2개
- Cypher 5개
- Neo4j import 경로와 Docker mount
- 카테고리, 이벤트 분류, 시대 범위, 인물 관계 생성 규칙

---

## 1. 전체 실행 흐름

```mermaid
flowchart TB
    runner["run_neo4j_preprocessing.py<br/>전체 전처리 시작 파일"]

    raw["Raw CSV<br/>원본 데이터"]
    normalize["normalize_raw_data.py<br/>raw 정규화"]
    base_dict["make_base_dictionaries.py<br/>1차 사전 생성"]
    mapping["make_mapping_tables.py<br/>mapping / staging 생성"]
    graph_csv["make_graph_csv.py<br/>Neo4j import CSV 생성"]
    import_dir["storage/neo4j/neo4j_import<br/>최종 import CSV"]
    schema_runner["storage/neo4j/load_schema.py<br/>Cypher 실행"]
    neo4j["Neo4j Graph DB"]

    runner --> normalize
    runner --> base_dict
    runner --> mapping
    runner --> graph_csv

    raw --> normalize
    normalize --> base_dict
    base_dict --> mapping
    normalize --> mapping
    normalize --> graph_csv
    base_dict --> graph_csv
    mapping --> graph_csv
    graph_csv --> import_dir
    import_dir --> schema_runner
    schema_runner --> neo4j
```

---

## 2. 폴더 구조

```mermaid
flowchart TB
    project["SKN27-FINAL-2Team"]

    raw_dir["etl/raw_data<br/>원본 CSV"]
    prep_dir["etl/preprocessing/neo4j<br/>전처리 루트"]
    storage_dir["storage/neo4j<br/>Neo4j 실행 / import / schema"]
    docs_dir["docs/neo4j<br/>설계 및 구현 문서"]

    scripts_dir["scripts<br/>전처리 코드"]
    seed_dir["seed<br/>수동 규칙 CSV"]
    normalized_dir["normalized<br/>정규화 CSV"]
    dictionary_dir["dictionary<br/>사전 CSV"]
    mapping_dir["mapping<br/>crosswalk CSV"]
    staging_dir["staging<br/>관계 중간 CSV"]

    import_root["neo4j_import<br/>Docker import mount 대상"]
    nodes_dir["neo4j_import/nodes<br/>최종 node CSV"]
    relations_dir["neo4j_import/relations<br/>최종 relationship CSV"]
    schema_dir["schema<br/>history_graph_*.cypher"]

    project --> raw_dir
    project --> prep_dir
    project --> storage_dir
    project --> docs_dir

    prep_dir --> scripts_dir
    prep_dir --> seed_dir
    prep_dir --> normalized_dir
    prep_dir --> dictionary_dir
    prep_dir --> mapping_dir
    prep_dir --> staging_dir

    storage_dir --> import_root
    import_root --> nodes_dir
    import_root --> relations_dir
    storage_dir --> schema_dir
```

---

## 3. Raw에서 Normalized까지

```mermaid
flowchart LR
    subgraph raw["Raw 입력 CSV"]
        raw_terms["교육부 국사편찬위원회_한국역사용어시소러스 정보_20211028 (1).csv"]
        raw_events["한국고전종합DB_관계망/itkc_events.csv"]
        raw_event_rel["한국고전종합DB_관계망/itkc_event_relations.csv"]
        raw_person_rel["한국고전종합DB_관계망/itkc_person_relations.csv"]
    end

    normalize["normalize_raw_data.py"]

    subgraph normalized["normalized CSV"]
        norm_terms["normalized/terms.csv<br/>term_kind=2 실제 용어"]
        norm_events["normalized/events.csv<br/>event_id 기준 사건"]
        norm_event_rel["normalized/event_relations.csv<br/>event-person 관계"]
        norm_person_rel["normalized/person_relations.csv<br/>person-person 관계"]
    end

    raw_terms --> normalize --> norm_terms
    raw_events --> normalize --> norm_events
    raw_event_rel --> normalize --> norm_event_rel
    raw_person_rel --> normalize --> norm_person_rel
```

---

## 4. Seed 규칙 입력

```mermaid
flowchart TB
    subgraph seed["seed CSV - 사람이 관리하는 규칙표"]
        category_axis_seed["category_axis_seed.csv<br/>country / economic_domain 축 추출 규칙"]
        country_seed["country_seed.csv<br/>국가 / 정치체"]
        region_seed["region_seed.csv<br/>권역 / 기타지역"]
        event_facet_seed["event_facet_seed.csv<br/>이벤트 facet 분류"]
        period_seed["period_seed.csv<br/>시대 순서 / 범위 확장"]
        relation_type_seed["relation_type_seed.csv<br/>인물 관계 정규화"]
        taxonomy_crosswalk_seed["taxonomy_crosswalk_seed.csv<br/>이벤트 분류 - 표준 카테고리 수동 매핑"]
    end

    base_dict["make_base_dictionaries.py"]
    mapping_script["make_mapping_tables.py"]
    graph_script["make_graph_csv.py"]

    period_seed --> base_dict
    relation_type_seed --> base_dict

    category_axis_seed --> mapping_script
    country_seed --> mapping_script
    region_seed --> mapping_script
    event_facet_seed --> mapping_script
    taxonomy_crosswalk_seed --> mapping_script

    base_dict --> graph_script
    mapping_script --> graph_script
```

---

## 5. Dictionary 생성 구조

```mermaid
flowchart TB
    norm_terms["normalized/terms.csv"]
    norm_events["normalized/events.csv"]
    norm_event_rel["normalized/event_relations.csv"]
    norm_person_rel["normalized/person_relations.csv"]

    period_seed["seed/period_seed.csv"]
    relation_type_seed["seed/relation_type_seed.csv"]
    event_facet_seed["seed/event_facet_seed.csv"]
    country_seed["seed/country_seed.csv"]
    region_seed["seed/region_seed.csv"]
    category_axis_seed["seed/category_axis_seed.csv"]

    base_dict["make_base_dictionaries.py"]
    mapping_script["make_mapping_tables.py"]

    subgraph dictionary["dictionary CSV"]
        canonical_dict["canonical_category_dictionary.csv"]
        source_event_cat_dict["source_event_category_dictionary.csv"]
        period_dict["period_dictionary.csv"]
        relation_type_dict["relation_type_dictionary.csv"]
        source_url_dict["source_url_dictionary.csv"]
        event_facet_dict["event_facet_dictionary.csv"]
        country_dict["country_dictionary.csv"]
        region_dict["region_dictionary.csv"]
        economic_domain_dict["economic_domain_dictionary.csv"]
        taxonomy_facet_dict["taxonomy_facet_dictionary.csv"]
    end

    norm_terms --> base_dict
    norm_events --> base_dict
    norm_event_rel --> base_dict
    norm_person_rel --> base_dict
    period_seed --> base_dict
    relation_type_seed --> base_dict

    base_dict --> canonical_dict
    base_dict --> source_event_cat_dict
    base_dict --> period_dict
    base_dict --> relation_type_dict
    base_dict --> source_url_dict

    canonical_dict --> mapping_script
    source_event_cat_dict --> mapping_script
    event_facet_seed --> mapping_script
    country_seed --> mapping_script
    region_seed --> mapping_script
    category_axis_seed --> mapping_script

    mapping_script --> event_facet_dict
    mapping_script --> country_dict
    mapping_script --> region_dict
    mapping_script --> economic_domain_dict
    mapping_script --> taxonomy_facet_dict
```

---

## 6. Mapping과 Staging 생성 구조

```mermaid
flowchart TB
    norm_terms["normalized/terms.csv"]
    norm_events["normalized/events.csv"]

    canonical_dict["dictionary/canonical_category_dictionary.csv"]
    source_event_cat_dict["dictionary/source_event_category_dictionary.csv"]
    event_facet_dict["dictionary/event_facet_dictionary.csv"]
    country_dict["dictionary/country_dictionary.csv"]
    region_dict["dictionary/region_dictionary.csv"]
    economic_domain_dict["dictionary/economic_domain_dictionary.csv"]
    taxonomy_facet_dict["dictionary/taxonomy_facet_dictionary.csv"]

    taxonomy_crosswalk_seed["seed/taxonomy_crosswalk_seed.csv"]
    event_facet_seed["seed/event_facet_seed.csv"]

    mapping_script["make_mapping_tables.py"]

    subgraph staging["staging CSV"]
        term_cat_rel["term_canonical_category_relation.csv"]
        event_source_cat_rel["event_source_category_relation.csv"]
        event_date_parse["event_date_parse.csv"]
    end

    subgraph mapping["mapping CSV"]
        taxonomy_crosswalk["taxonomy_crosswalk.csv"]
        source_event_facet_crosswalk["source_event_category_facet_crosswalk.csv"]
        category_country_crosswalk["canonical_category_country_crosswalk.csv"]
        category_region_crosswalk["canonical_category_region_crosswalk.csv"]
        category_economic_crosswalk["canonical_category_economic_domain_crosswalk.csv"]
        category_taxonomy_facet_crosswalk["canonical_category_taxonomy_facet_crosswalk.csv"]
    end

    norm_terms --> mapping_script
    norm_events --> mapping_script
    canonical_dict --> mapping_script
    source_event_cat_dict --> mapping_script
    event_facet_dict --> mapping_script
    country_dict --> mapping_script
    region_dict --> mapping_script
    economic_domain_dict --> mapping_script
    taxonomy_facet_dict --> mapping_script
    taxonomy_crosswalk_seed --> mapping_script
    event_facet_seed --> mapping_script

    mapping_script --> term_cat_rel
    mapping_script --> event_source_cat_rel
    mapping_script --> taxonomy_crosswalk
    mapping_script --> source_event_facet_crosswalk
    mapping_script --> category_country_crosswalk
    mapping_script --> category_region_crosswalk
    mapping_script --> category_economic_crosswalk
    mapping_script --> category_taxonomy_facet_crosswalk

    norm_events --> event_date_parse
```

---

## 7. 최종 Node CSV 생성 구조

```mermaid
flowchart TB
    graph_script["make_graph_csv.py"]

    subgraph inputs["입력 CSV"]
        norm_terms["normalized/terms.csv"]
        norm_events["normalized/events.csv"]
        norm_event_rel["normalized/event_relations.csv"]
        norm_person_rel["normalized/person_relations.csv"]
        canonical_dict["dictionary/canonical_category_dictionary.csv"]
        source_event_cat_dict["dictionary/source_event_category_dictionary.csv"]
        period_dict["dictionary/period_dictionary.csv"]
        source_url_dict["dictionary/source_url_dictionary.csv"]
        event_facet_dict["dictionary/event_facet_dictionary.csv"]
        country_dict["dictionary/country_dictionary.csv"]
        region_dict["dictionary/region_dictionary.csv"]
        economic_domain_dict["dictionary/economic_domain_dictionary.csv"]
        taxonomy_facet_dict["dictionary/taxonomy_facet_dictionary.csv"]
        event_date_parse["staging/event_date_parse.csv"]
    end

    subgraph nodes["storage/neo4j/neo4j_import/nodes"]
        terms_node["terms.csv<br/>Term"]
        events_node["events.csv<br/>Event"]
        people_node["people.csv<br/>Person"]
        canonical_node["canonical_categories.csv<br/>CanonicalCategory"]
        source_event_node["source_event_categories.csv<br/>SourceEventCategory"]
        periods_node["periods.csv<br/>Period"]
        source_urls_node["source_urls.csv<br/>SourceUrl"]
        event_groups_node["event_groups.csv<br/>EventGroup"]
        event_facets_node["event_facets.csv<br/>EventFacet"]
        countries_node["countries.csv<br/>Country"]
        regions_node["regions.csv<br/>Region"]
        economic_domains_node["economic_domains.csv<br/>EconomicDomain"]
        taxonomy_facets_node["taxonomy_facets.csv<br/>TaxonomyFacet"]
        search_tags_node["search_tags.csv<br/>SearchTag"]
    end

    inputs --> graph_script

    graph_script --> terms_node
    graph_script --> events_node
    graph_script --> people_node
    graph_script --> canonical_node
    graph_script --> source_event_node
    graph_script --> periods_node
    graph_script --> source_urls_node
    graph_script --> event_groups_node
    graph_script --> event_facets_node
    graph_script --> countries_node
    graph_script --> regions_node
    graph_script --> economic_domains_node
    graph_script --> taxonomy_facets_node
    graph_script --> search_tags_node
```

---

## 8. 최종 Relationship CSV 생성 구조

```mermaid
flowchart TB
    graph_script["make_graph_csv.py"]

    subgraph input_groups["입력 묶음"]
        normalized_group["normalized CSV<br/>terms / events / event_relations / person_relations"]
        dictionary_group["dictionary CSV<br/>10개 사전"]
        mapping_group["mapping CSV<br/>6개 crosswalk"]
        staging_group["staging CSV<br/>3개 중간 산출물"]
        node_group["node outputs<br/>EventGroup / SearchTag lookup 포함"]
    end

    subgraph relations["storage/neo4j/neo4j_import/relations"]
        r01["term_has_canonical_category.csv"]
        r02["term_in_period.csv"]
        r03["term_about_country.csv"]
        r04["term_about_region.csv"]
        r05["term_about_economic_domain.csv"]
        r06["term_about_taxonomy_facet.csv"]

        r07["event_has_source_category.csv"]
        r08["event_has_canonical_category.csv"]
        r09["event_has_facet.csv"]
        r10["event_in_period.csv"]
        r11["event_part_of_event_group.csv"]
        r12["event_has_source_url.csv"]
        r13["event_has_search_tag.csv"]
        r14["event_about_country.csv"]
        r15["event_about_taxonomy_facet.csv"]

        r16["person_involved_in_event.csv"]
        r17["person_related_to_person.csv"]
        r18["person_has_source_url.csv"]

        r19["canonical_category_subcategory_of.csv"]
        r20["source_category_mapped_to_canonical_category.csv"]
        r21["canonical_category_about_country.csv"]
        r22["canonical_category_about_region.csv"]
        r23["canonical_category_about_economic_domain.csv"]
        r24["canonical_category_about_taxonomy_facet.csv"]
        r25["region_subregion_of.csv"]
    end

    subgraph optional_relations["optional relation CSV - 0행이면 미생성"]
        opt_r01["event_about_region.csv"]
        opt_r02["event_about_economic_domain.csv"]
    end

    normalized_group --> graph_script
    dictionary_group --> graph_script
    mapping_group --> graph_script
    staging_group --> graph_script
    node_group --> graph_script

    graph_script --> r01
    graph_script --> r02
    graph_script --> r03
    graph_script --> r04
    graph_script --> r05
    graph_script --> r06
    graph_script --> r07
    graph_script --> r08
    graph_script --> r09
    graph_script --> r10
    graph_script --> r11
    graph_script --> r12
    graph_script --> r13
    graph_script --> r14
    graph_script --> r15
    graph_script --> r16
    graph_script --> r17
    graph_script --> r18
    graph_script --> r19
    graph_script --> r20
    graph_script --> r21
    graph_script --> r22
    graph_script --> r23
    graph_script --> r24
    graph_script --> r25
    graph_script -.-> opt_r01
    graph_script -.-> opt_r02
```

---

## 9. Neo4j 그래프 스키마

```mermaid
flowchart TB
    optional_relation["optional event semantic relation<br/>event_about_region.csv<br/>event_about_economic_domain.csv"]
    current_mapping["현재 taxonomy_crosswalk.csv<br/>이벤트 표준 카테고리 매핑"]
    no_rows["현재 결과 0행<br/>Region / EconomicDomain 축으로 이어지는 이벤트 없음"]
    skip_csv["make_graph_csv.py<br/>0행이면 CSV 미생성<br/>기존 stale CSV 삭제"]
    keep_cypher["history_graph_import_relations.cypher<br/>LOAD 블록은 유지"]
    loader_skip["load_schema.py<br/>파일이 없으면 optional LOAD만 skip"]
    future_rows["나중에 seed/mapping 보강<br/>행이 생기면 CSV 자동 생성"]
    future_import["같은 Cypher 블록으로 import 가능"]

    optional_relation --> current_mapping --> no_rows --> skip_csv
    skip_csv --> loader_skip
    keep_cypher --> loader_skip
    future_rows --> future_import
    keep_cypher --> future_import
```

이렇게 구현한 이유는 최종 import 폴더를 실제 적재 대상만 남기는 곳으로 유지하기 위해서다. 0행 CSV를 남겨두면 실제 관계가 존재하는 것처럼 보이고, 검수 시 누락인지 의도된 빈 결과인지 계속 확인해야 한다. 반대로 Cypher 블록까지 완전히 삭제하면 나중에 매핑이 보강되어 행이 생겼을 때 import 구조를 다시 손봐야 한다. 그래서 현재 구현은 빈 CSV는 만들지 않고, `load_schema.py`에서 optional LOAD만 건너뛰는 절충 구조다.

---

## 10. Neo4j 그래프 스키마

```mermaid
flowchart LR
    term["Term<br/>terms.csv"]
    event["Event<br/>events.csv"]
    person["Person<br/>people.csv"]
    category["CanonicalCategory<br/>canonical_categories.csv"]
    source_cat["SourceEventCategory<br/>source_event_categories.csv"]
    period["Period<br/>periods.csv"]
    source_url["SourceUrl<br/>source_urls.csv"]
    event_group["EventGroup<br/>event_groups.csv"]
    event_facet["EventFacet<br/>event_facets.csv"]
    country["Country<br/>countries.csv"]
    region["Region<br/>regions.csv"]
    economic["EconomicDomain<br/>economic_domains.csv"]
    taxonomy_facet["TaxonomyFacet<br/>taxonomy_facets.csv"]
    search_tag["SearchTag<br/>search_tags.csv"]

    term -->|"HAS_CATEGORY<br/>term_has_canonical_category.csv"| category
    term -->|"IN_PERIOD<br/>term_in_period.csv"| period
    term -->|"ABOUT_COUNTRY<br/>term_about_country.csv"| country
    term -->|"ABOUT_REGION<br/>term_about_region.csv"| region
    term -->|"ABOUT_ECONOMIC_DOMAIN<br/>term_about_economic_domain.csv"| economic
    term -->|"ABOUT_TAXONOMY_FACET<br/>term_about_taxonomy_facet.csv"| taxonomy_facet

    event -->|"HAS_EVENT_CATEGORY<br/>event_has_source_category.csv"| source_cat
    event -->|"HAS_CATEGORY<br/>event_has_canonical_category.csv"| category
    event -->|"HAS_EVENT_FACET<br/>event_has_facet.csv"| event_facet
    event -->|"IN_PERIOD<br/>event_in_period.csv"| period
    event -->|"PART_OF_EVENT_GROUP<br/>event_part_of_event_group.csv"| event_group
    event -->|"HAS_SOURCE_URL<br/>event_has_source_url.csv"| source_url
    event -->|"HAS_SEARCH_TAG<br/>event_has_search_tag.csv"| search_tag
    event -->|"ABOUT_COUNTRY<br/>event_about_country.csv"| country
    opt_event_region["ABOUT_REGION optional<br/>event_about_region.csv<br/>0행이면 미생성"]
    opt_event_economic["ABOUT_ECONOMIC_DOMAIN optional<br/>event_about_economic_domain.csv<br/>0행이면 미생성"]

    event -.-> opt_event_region
    opt_event_region -.-> region
    event -.-> opt_event_economic
    opt_event_economic -.-> economic
    event -->|"ABOUT_TAXONOMY_FACET<br/>event_about_taxonomy_facet.csv"| taxonomy_facet

    person -->|"INVOLVED_IN<br/>person_involved_in_event.csv"| event
    person -->|"RELATED_TO<br/>person_related_to_person.csv"| person
    person -->|"HAS_SOURCE_URL<br/>person_has_source_url.csv"| source_url

    category -->|"SUBCATEGORY_OF<br/>canonical_category_subcategory_of.csv"| category
    source_cat -->|"MAPPED_TO_CATEGORY<br/>source_category_mapped_to_canonical_category.csv"| category
    category -->|"ABOUT_COUNTRY<br/>canonical_category_about_country.csv"| country
    category -->|"ABOUT_REGION<br/>canonical_category_about_region.csv"| region
    category -->|"ABOUT_ECONOMIC_DOMAIN<br/>canonical_category_about_economic_domain.csv"| economic
    category -->|"ABOUT_TAXONOMY_FACET<br/>canonical_category_about_taxonomy_facet.csv"| taxonomy_facet
    region -->|"SUBREGION_OF<br/>region_subregion_of.csv"| region
```

---

## 11. 카테고리 분해와 의미 축 분리

```mermaid
flowchart TB
    term_lk["history_terms.term_lk<br/>예: 경제·산업>수산업>수산업일반<br/>예: A>B>>C>D"]

    split_multi["1. >> 기준 복수 경로 분리"]
    split_depth["2. > 기준 depth 분리"]
    build_paths["3. depth별 category_path 생성<br/>경제·산업<br/>경제·산업>수산업<br/>경제·산업>수산업>수산업일반"]
    canonical_dict["canonical_category_dictionary.csv"]
    term_relation["term_canonical_category_relation.csv<br/>용어는 leaf category에 직접 연결"]
    subcategory_rel["canonical_category_subcategory_of.csv<br/>카테고리 계층 연결"]

    country_axis["국가 축<br/>country_dictionary.csv<br/>canonical_category_country_crosswalk.csv"]
    region_axis["지역 축<br/>region_dictionary.csv<br/>canonical_category_region_crosswalk.csv"]
    economic_axis["경제 분야 축<br/>economic_domain_dictionary.csv<br/>canonical_category_economic_domain_crosswalk.csv"]
    taxonomy_axis["중간 taxonomy facet<br/>taxonomy_facet_dictionary.csv<br/>canonical_category_taxonomy_facet_crosswalk.csv"]

    term_lk --> split_multi --> split_depth --> build_paths --> canonical_dict
    canonical_dict --> term_relation
    canonical_dict --> subcategory_rel
    canonical_dict --> country_axis
    canonical_dict --> region_axis
    canonical_dict --> economic_axis
    canonical_dict --> taxonomy_axis
```

---

## 12. 이벤트 분류 표준화 흐름

```mermaid
flowchart TB
    event_subject["events.subject_category<br/>원천 이벤트 분류"]
    split_event_cat["쉼표 / 줄바꿈 기준 분리"]
    source_event_dict["source_event_category_dictionary.csv"]
    event_source_relation["event_source_category_relation.csv"]
    event_has_source["event_has_source_category.csv<br/>Event - HAS_EVENT_CATEGORY - SourceEventCategory"]

    taxonomy_seed["taxonomy_crosswalk_seed.csv"]
    taxonomy_crosswalk["taxonomy_crosswalk.csv<br/>EXACT_NAME + 수동 seed"]
    source_to_canonical["source_category_mapped_to_canonical_category.csv"]
    event_has_canonical["event_has_canonical_category.csv<br/>Event - HAS_CATEGORY - CanonicalCategory"]

    facet_seed["event_facet_seed.csv"]
    event_facet_dict["event_facet_dictionary.csv"]
    source_event_facet_crosswalk["source_event_category_facet_crosswalk.csv"]
    event_has_facet["event_has_facet.csv<br/>Event - HAS_EVENT_FACET - EventFacet"]

    search_tag["event_has_search_tag.csv<br/>검색 편의용 통합 태그"]

    event_subject --> split_event_cat --> source_event_dict
    split_event_cat --> event_source_relation --> event_has_source

    source_event_dict --> taxonomy_crosswalk
    taxonomy_seed --> taxonomy_crosswalk
    taxonomy_crosswalk --> source_to_canonical
    taxonomy_crosswalk --> event_has_canonical

    source_event_dict --> source_event_facet_crosswalk
    facet_seed --> event_facet_dict
    facet_seed --> source_event_facet_crosswalk
    event_facet_dict --> event_has_facet

    event_has_source --> search_tag
    event_has_canonical --> search_tag
    event_has_facet --> search_tag
```

---

## 13. 시대 범위 확장 흐름

```mermaid
flowchart TB
    term_times["terms.term_times<br/>예: 삼국시대-조선시대"]
    event_period["events.period<br/>예: 고려"]
    period_seed["period_seed.csv<br/>range_group / period_order / is_range_expansion_candidate"]
    period_dict["period_dictionary.csv"]

    split_period["시대 표현 분리<br/>쉼표 / 줄바꿈 / 범위 기호"]
    direct["DIRECT<br/>단일 시대"]
    range_start["RANGE_START<br/>범위 시작"]
    range_middle["RANGE_MIDDLE<br/>중간 시대 자동 확장"]
    range_end["RANGE_END<br/>범위 끝"]

    term_in_period["term_in_period.csv<br/>Term - IN_PERIOD - Period"]
    event_in_period["event_in_period.csv<br/>Event - IN_PERIOD - Period"]

    term_times --> split_period
    event_period --> split_period
    period_seed --> period_dict --> split_period

    split_period --> direct
    split_period --> range_start
    split_period --> range_middle
    split_period --> range_end

    direct --> term_in_period
    range_start --> term_in_period
    range_middle --> term_in_period
    range_end --> term_in_period

    direct --> event_in_period
    range_start --> event_in_period
    range_middle --> event_in_period
    range_end --> event_in_period
```

---

## 14. 인물 그래프 생성 흐름

```mermaid
flowchart TB
    event_rel["normalized/event_relations.csv<br/>event_id / person_id / relation_type"]
    person_rel["normalized/person_relations.csv<br/>person_id / related_person_id / relation_type"]
    relation_seed["relation_type_seed.csv"]
    relation_dict["relation_type_dictionary.csv"]

    people_from_event["event_relations.person_id<br/>사건 참여자"]
    people_from_person["person_relations.person_id<br/>관계 출발 인물"]
    people_from_related["person_relations.related_person_id<br/>관계 대상 인물"]
    people_node["people.csv<br/>Person"]

    involved["person_involved_in_event.csv<br/>Person - INVOLVED_IN - Event"]
    related["person_related_to_person.csv<br/>Person - RELATED_TO - Person<br/>raw / normalized / direction 속성 보존"]
    person_url["person_has_source_url.csv<br/>Person - HAS_SOURCE_URL - SourceUrl"]

    event_rel --> people_from_event --> people_node
    person_rel --> people_from_person --> people_node
    person_rel --> people_from_related --> people_node

    event_rel --> involved
    person_rel --> related
    relation_seed --> relation_dict --> related
    person_rel --> person_url
```

---

## 15. URL과 RAG 후보 흐름

```mermaid
flowchart TB
    event_urls["events.source_urls"]
    event_relation_urls["event_relations.source_urls"]
    person_evidence["person_relations.evidence_url"]
    person_detail["person_relations.detail_url"]

    source_url_dict["source_url_dictionary.csv<br/>use_for_rag=Y<br/>fetch_status=PENDING"]
    source_urls_node["source_urls.csv<br/>SourceUrl"]

    event_has_url["event_has_source_url.csv<br/>Event - HAS_SOURCE_URL - SourceUrl"]
    person_has_url["person_has_source_url.csv<br/>Person - HAS_SOURCE_URL - SourceUrl"]
    web_rag["Web RAG / Tavily 후보<br/>그래프에서 URL을 찾고 외부 문서 수집 가능"]

    event_urls --> source_url_dict
    event_relation_urls --> source_url_dict
    person_evidence --> source_url_dict
    person_detail --> source_url_dict

    source_url_dict --> source_urls_node
    source_urls_node --> event_has_url
    source_urls_node --> person_has_url
    source_urls_node --> web_rag
```

---

## 16. Neo4j import와 Cypher 실행 흐름

```mermaid
flowchart TB
    docker["docker-compose.yml<br/>./neo4j_import:/var/lib/neo4j/import"]
    import_nodes["storage/neo4j/neo4j_import/nodes/*.csv"]
    import_relations["storage/neo4j/neo4j_import/relations/*.csv"]
    container_import["Neo4j container<br/>file:///nodes/*.csv<br/>file:///relations/*.csv"]

    load_schema["load_schema.py"]

    reset["history_graph_reset.cypher<br/>항상 실행<br/>기존 노드/관계 전체 배치 삭제"]
    constraints["history_graph_constraints.cypher<br/>unique constraint / index 생성"]
    import_node_cypher["history_graph_import_nodes.cypher<br/>node CSV import"]
    import_relation_cypher["history_graph_import_relations.cypher<br/>relationship CSV import"]
    verify["history_graph_verify.cypher<br/>노드/관계 수 검증"]

    neo4j["Neo4j Graph DB"]

    docker --> container_import
    import_nodes --> container_import
    import_relations --> container_import

    container_import --> load_schema

    load_schema --> reset
    reset --> constraints
    constraints --> import_node_cypher
    import_node_cypher --> import_relation_cypher
    import_relation_cypher --> verify
    verify --> neo4j
```

---

## 17. Constraint와 Index 구조

```mermaid
flowchart TB
    constraints["history_graph_constraints.cypher"]

    subgraph unique["Unique constraints"]
        u_term["Term.term_id"]
        u_event["Event.event_id"]
        u_person["Person.person_id"]
        u_category["CanonicalCategory.category_id"]
        u_source_cat["SourceEventCategory.event_category_id"]
        u_period["Period.period_id"]
        u_source_url["SourceUrl.source_url_id"]
        u_event_group["EventGroup.event_group_id"]
        u_event_facet["EventFacet.event_facet_id"]
        u_country["Country.country_id"]
        u_region["Region.region_id"]
        u_economic["EconomicDomain.economic_domain_id"]
        u_taxonomy["TaxonomyFacet.taxonomy_facet_id"]
        u_search_tag["SearchTag.search_tag_id"]
    end

    subgraph indexes["Lookup indexes"]
        i_term["Term.name"]
        i_event["Event.name"]
        i_person["Person.name"]
        i_category_path["CanonicalCategory.category_path"]
        i_url["SourceUrl.url"]
    end

    constraints --> unique
    constraints --> indexes
```

---

## 18. 최종 산출물에서 Neo4j까지 한눈에 보기

```mermaid
flowchart LR
    subgraph node_csv["Node CSV 14개"]
        n1["terms.csv"]
        n2["events.csv"]
        n3["people.csv"]
        n4["canonical_categories.csv"]
        n5["source_event_categories.csv"]
        n6["periods.csv"]
        n7["source_urls.csv"]
        n8["event_groups.csv"]
        n9["event_facets.csv"]
        n10["countries.csv"]
        n11["regions.csv"]
        n12["economic_domains.csv"]
        n13["taxonomy_facets.csv"]
        n14["search_tags.csv"]
    end

    subgraph relation_csv["Relationship CSV 25개"]
        e1["term_has_canonical_category.csv"]
        e2["term_in_period.csv"]
        e3["term_about_country.csv"]
        e4["term_about_region.csv"]
        e5["term_about_economic_domain.csv"]
        e6["term_about_taxonomy_facet.csv"]
        e7["event_has_source_category.csv"]
        e8["event_has_canonical_category.csv"]
        e9["event_has_facet.csv"]
        e10["event_in_period.csv"]
        e11["event_part_of_event_group.csv"]
        e12["event_has_source_url.csv"]
        e13["event_has_search_tag.csv"]
        e14["event_about_country.csv"]
        e15["event_about_taxonomy_facet.csv"]
        e16["person_involved_in_event.csv"]
        e17["person_related_to_person.csv"]
        e18["person_has_source_url.csv"]
        e19["canonical_category_subcategory_of.csv"]
        e20["source_category_mapped_to_canonical_category.csv"]
        e21["canonical_category_about_country.csv"]
        e22["canonical_category_about_region.csv"]
        e23["canonical_category_about_economic_domain.csv"]
        e24["canonical_category_about_taxonomy_facet.csv"]
        e25["region_subregion_of.csv"]
    end

    subgraph optional_relation_csv["Optional relationship CSV 2개"]
        opt_e01["event_about_region.csv<br/>현재 0행이라 미생성"]
        opt_e02["event_about_economic_domain.csv<br/>현재 0행이라 미생성"]
    end

    import_nodes["history_graph_import_nodes.cypher"]
    import_relations["history_graph_import_relations.cypher"]
    graph["Neo4j Graph DB"]

    node_csv --> import_nodes --> graph
    relation_csv --> import_relations --> graph
    optional_relation_csv -.-> import_relations
```
