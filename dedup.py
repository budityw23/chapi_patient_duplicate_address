import re
import copy

ADMIN_URL_ORDER = ("province", "city", "district", "village")


def _normalize_line(address):
    lines = address.get("line") or []
    parts = []
    for line in lines:
        normalized = re.sub(r"\s+", " ", line.strip()).lower()
        if normalized:
            parts.append(normalized)
    return " / ".join(parts)


def _get_admin_code(address):
    for ext in address.get("extension", []):
        if ext.get("url") == "administrativeCode":
            sub = {s["url"]: s.get("valueString") for s in ext.get("extension", [])}
            return (
                sub.get("province"),
                sub.get("city"),
                sub.get("district"),
                sub.get("village"),
            )
    return (None, None, None, None)


def _admin_code_depth(code):
    depth = 0
    for v in code:
        if v is None:
            break
        depth += 1
    return depth


def _admin_codes_compatible(a, b):
    min_d = min(_admin_code_depth(a), _admin_code_depth(b))
    return a[:min_d] == b[:min_d]


def _lines_compatible(norm_a, norm_b):
    return not (norm_a and norm_b and norm_a != norm_b)


def _addresses_compatible(a, b):
    if a.get("use") != b.get("use"):
        return False
    if not _lines_compatible(_normalize_line(a), _normalize_line(b)):
        return False
    if not _admin_codes_compatible(_get_admin_code(a), _get_admin_code(b)):
        return False
    return True


def _score(address):
    score = 0
    code = _get_admin_code(address)
    if code[3] is not None:
        score += 8
    elif code[2] is not None:
        score += 4
    elif code[1] is not None:
        score += 2
    elif code[0] is not None:
        score += 1
    if _normalize_line(address):
        score += 2
    for field in ("city", "state", "postalCode", "country"):
        if address.get(field):
            score += 1
    period = address.get("period") or {}
    if not period.get("end"):
        score += 1
    if address.get("text"):
        score += 1
    return score


def _sort_admin_code_extensions(address):
    for ext in address.get("extension", []):
        if ext.get("url") == "administrativeCode":
            subs = ext.get("extension", [])
            subs.sort(
                key=lambda s: (
                    ADMIN_URL_ORDER.index(s["url"])
                    if s["url"] in ADMIN_URL_ORDER
                    else len(ADMIN_URL_ORDER)
                )
            )
    return address


def dedup_addresses(addresses):
    n = len(addresses)
    if n <= 1:
        return {
            "deduped": list(addresses),
            "dropped_indices": [],
            "kept_indices": list(range(n)),
        }

    # Greedy clique grouping: each address joins the first group whose
    # every member is compatible with it; otherwise starts a new group.
    groups = []
    for i, addr in enumerate(addresses):
        placed = False
        for group in groups:
            if all(_addresses_compatible(addr, addresses[j]) for j in group):
                group.append(i)
                placed = True
                break
        if not placed:
            groups.append([i])

    kept_set = set()
    for group in groups:
        winner = max(group, key=lambda i: (_score(addresses[i]), i))
        kept_set.add(winner)

    kept_indices = sorted(kept_set)
    dropped_indices = [i for i in range(n) if i not in kept_set]

    deduped = []
    for i in kept_indices:
        addr = copy.deepcopy(addresses[i])
        _sort_admin_code_extensions(addr)
        deduped.append(addr)

    return {
        "deduped": deduped,
        "dropped_indices": dropped_indices,
        "kept_indices": kept_indices,
    }


# ---------------------------------------------------------------------------
# Module-level self-tests — run at import time; container won't start on fail
# ---------------------------------------------------------------------------

def _admin_ext(province=None, city=None, district=None, village=None):
    subs = []
    if province is not None:
        subs.append({"url": "province", "valueString": province})
    if city is not None:
        subs.append({"url": "city", "valueString": city})
    if district is not None:
        subs.append({"url": "district", "valueString": district})
    if village is not None:
        subs.append({"url": "village", "valueString": village})
    return [{"url": "administrativeCode", "extension": subs}]


# Case 1: all-distinct use → nothing dropped
_t1 = [{"use": "home"}, {"use": "work"}]
_r1 = dedup_addresses(_t1)
assert _r1["dropped_indices"] == [], f"Case 1 failed: {_r1}"
assert _r1["kept_indices"] == [0, 1], f"Case 1 failed: {_r1}"

# Case 2: two home, one province-only, one full village (prefix-compatible) → village wins
_t2 = [
    {"use": "home", "extension": _admin_ext(province="33")},
    {"use": "home", "extension": _admin_ext(province="33", city="3303", district="330301", village="3303012019")},
]
_r2 = dedup_addresses(_t2)
assert _r2["dropped_indices"] == [0], f"Case 2 failed: {_r2}"
assert _r2["kept_indices"] == [1], f"Case 2 failed: {_r2}"

# Case 3: two home with incompatible province → nothing dropped
_t3 = [
    {"use": "home", "extension": _admin_ext(province="33")},
    {"use": "home", "extension": _admin_ext(province="34")},
]
_r3 = dedup_addresses(_t3)
assert _r3["dropped_indices"] == [], f"Case 3 failed: {_r3}"
assert _r3["kept_indices"] == [0, 1], f"Case 3 failed: {_r3}"

# Case 4: same admin codes, one has more top-level fields → more detailed wins
_t4 = [
    {"use": "home", "extension": _admin_ext(province="33", city="3303")},
    {"use": "home", "city": "Purbalingga", "state": "Jawa Tengah",
     "extension": _admin_ext(province="33", city="3303")},
]
_r4 = dedup_addresses(_t4)
assert _r4["dropped_indices"] == [0], f"Case 4 failed: {_r4}"
assert _r4["kept_indices"] == [1], f"Case 4 failed: {_r4}"

# Case 5: score tie (identical by every signal) → highest array index wins
_t5 = [{"use": "home"}, {"use": "home"}]
_r5 = dedup_addresses(_t5)
assert _r5["dropped_indices"] == [0], f"Case 5 failed: {_r5}"
assert _r5["kept_indices"] == [1], f"Case 5 failed: {_r5}"
