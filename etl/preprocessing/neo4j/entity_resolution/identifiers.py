from hashlib import new as new_hash
from json import dumps


def create_stable_id(prefix: str, parts: list[str], identifier_policy: dict) -> str:
    """정책에 지정된 해시로 staging 식별자를 결정적으로 생성한다."""
    serialized = dumps(parts, ensure_ascii=False, separators=(",", ":"))
    hasher = new_hash(identifier_policy["hash_algorithm"])
    hasher.update(serialized.encode("utf-8"))
    digest_length = identifier_policy["digest_length"]
    return f"{prefix}{hasher.hexdigest()[:digest_length]}"
