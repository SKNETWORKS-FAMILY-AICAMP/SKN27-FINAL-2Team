import re


def get_topic_categories():
    return (
        "사건",
        "인물",
        "정치",
        "제도",
        "문화",
        "사회",
        "군사",
        "경제",
        "사상 종교",
        "외교",
    )


def get_era_categories():
    return (
        "조선",
        "고려",
        "삼국시대",
        "개항기",
        "현대",
        "일제강점기",
        "남북국 시대",
        "초기 국가",
        "선사 시대",
        "고조선",
    )


def normalize_classification_value(field_name, value):
    if field_name == "era":
        return normalize_era_category(value)
    elif field_name == "topic":
        return normalize_topic_category(value)

    return value or ""


def should_normalize_classification(field_name):
    return field_name in ("era", "topic")


def normalize_era_category(value):
    return normalize_category_value(
        value,
        get_era_categories(),
        get_era_aliases(),
    )


def normalize_topic_category(value):
    return normalize_category_value(
        value,
        get_topic_categories(),
        get_topic_aliases(),
    )


def normalize_category_value(value, categories, aliases):
    category_key = normalize_category_key(value)
    if not category_key:
        return ""

    for category in categories:
        if category_key == normalize_category_key(category):
            return category

    for category, alias_values in aliases.items():
        for alias in alias_values:
            alias_key = normalize_category_key(alias)
            if category_key == alias_key:
                return category
            elif alias_key and alias_key in category_key:
                return category

    return ""


def normalize_category_key(value):
    if value is None:
        return ""

    return re.sub(r"[\s·ㆍ/()_\-]+", "", str(value).strip()).lower()


def get_topic_aliases():
    return {
        "사건": ("사건",),
        "인물": ("인물",),
        "정치": ("정치",),
        "제도": ("제도",),
        "문화": ("문화",),
        "사회": ("사회",),
        "군사": ("군사", "전쟁", "전투", "국방"),
        "경제": ("경제",),
        "사상 종교": ("사상 종교", "사상", "종교", "불교", "유교", "성리학"),
        "외교": ("외교", "대외", "국제"),
    }


def get_era_aliases():
    return {
        "고조선": ("고조선",),
        "남북국 시대": ("남북국시대", "남북국", "통일신라", "발해"),
        "초기 국가": ("초기국가", "부여", "삼한", "옥저", "동예"),
        "선사 시대": ("선사시대", "선사", "구석기", "신석기", "청동기"),
        "삼국시대": ("삼국시대", "삼국", "고구려", "백제", "신라", "가야"),
        "일제강점기": ("일제강점기", "일제강점", "일제", "식민지"),
        "개항기": ("개항기", "개화기", "대한제국", "일제강점기이전근대", "근대"),
        "현대": ("현대", "대한민국", "광복이후", "해방이후"),
        "고려": ("고려", "고려시대"),
        "조선": ("조선", "조선시대", "조선전기", "조선후기", "조선중기"),
    }
