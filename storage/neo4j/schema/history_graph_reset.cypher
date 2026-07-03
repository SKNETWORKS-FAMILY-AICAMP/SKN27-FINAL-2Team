MATCH (n)
WHERE any(
    node_label IN labels(n)
    WHERE node_label IN [
        'Term',
        'Event',
        'Person',
        'CanonicalCategory',
        'SourceEventCategory',
        'Period',
        'SourceUrl',
        'EventGroup',
        'EventFacet',
        'Country',
        'Region',
        'EconomicDomain',
        'TaxonomyFacet',
        'SearchTag',
        'TermName',
        'TermTimes',
        'TermLink',
        'CategoryName',
        'SubjectCategory'
    ]
)
DETACH DELETE n;
