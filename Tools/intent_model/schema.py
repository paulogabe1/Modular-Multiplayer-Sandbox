"""Label space and validity rules for the intent model.

Vocabulary-agnostic by construction: no ability name is written in this file. Every
name, range and keyword is read from catalogue.json, which is exported from the
game's own token asset. A seventh ability is a seventh dictionary entry, nothing more.

The model's entire output surface is:

    {"tokens": [{"name": "Fireball", "params": {"Damage": "higher"}}]}

Names and bucket labels only. No numbers ever leave the model, which is why it cannot
produce an out-of-range ability: the C++ side maps the bucket through ParamRanges and
clamps. That mapping is mirrored here so training, evaluation and serving all agree.
"""

from __future__ import annotations

import json
from pathlib import Path

CATALOGUE_PATH = Path(__file__).with_name("catalogue.json")

# Ordered worst-to-best so a confusion matrix has a natural axis. "default" is the
# neutral label: an unmentioned parameter, and the most common one in real phrasing.
BUCKETS: tuple[str, ...] = ("much_lower", "lower", "default", "higher", "max")
BUCKET_TO_INDEX = {bucket: i for i, bucket in enumerate(BUCKETS)}
INDEX_TO_BUCKET = {i: bucket for bucket, i in BUCKET_TO_INDEX.items()}
DEFAULT_BUCKET = "default"
DEFAULT_INDEX = BUCKET_TO_INDEX[DEFAULT_BUCKET]

# The four buckets that mean "this parameter is being moved". `default` is excluded on
# purpose: "is this parameter mentioned at all" and "which way does it move" are separate
# questions, and a single 5-way softmax forces them to compete. The bucket heads are
# indexed by these four, and the full 5-way distribution is assembled at decode time, so
# every caller still does a plain argmax over `BUCKETS` order.
MOVED_BUCKETS: tuple[str, ...] = tuple(b for b in BUCKETS if b != DEFAULT_BUCKET)
MOVED_INDICES: tuple[int, ...] = tuple(BUCKET_TO_INDEX[b] for b in MOVED_BUCKETS)
MOVED_TO_INDEX = {bucket: i for i, bucket in enumerate(MOVED_BUCKETS)}
INDEX_TO_MOVED = {i: bucket for bucket, i in MOVED_TO_INDEX.items()}

# Mirrors ULoadoutValidator::GetCategoryCap (Defense => 1, everything else unbounded
# in practice). Kept data-driven so a future cap change is a JSON edit, not a code edit.
FALLBACK_CAPS: dict[str, int] = {"Defense": 1}


def load_catalogue(path: str | Path = CATALOGUE_PATH) -> dict:
    """Return the raw catalogue document."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def tokens_by_name(catalogue: dict) -> dict[str, dict]:
    """Index the catalogue's token list by token name, preserving file order."""
    return {token["name"]: token for token in catalogue["tokens"]}


def category_caps(catalogue: dict) -> dict[str, int]:
    """Per-category equip cap from the catalogue, falling back to the C++ default."""
    caps = dict(FALLBACK_CAPS)
    caps.update(catalogue.get("category_caps") or {})
    return caps


def param_ranges(token: dict) -> dict[str, dict]:
    """The token's {param_name: {min, max, default}} map, in declaration order."""
    return token["ranges"]


def parameter_vocabulary(catalogue: dict) -> list[str]:
    """Every parameter name used by any token, deduplicated, in first-seen order.

    This is the vocabulary the parameter-name embedding is trained over. It is what
    lets one shared bucket head serve `Damage` for Fireball and `Distance` for a token
    that did not exist when the model was trained.
    """
    seen: list[str] = []
    for token in catalogue["tokens"]:
        for param_name in token["ranges"]:
            if param_name not in seen:
                seen.append(param_name)
    return seen


def bucket_to_value(param_range: dict, bucket: str) -> float:
    """Map a bucket label onto a concrete value inside the parameter's range.

    `much_lower` -> Min, `lower` -> half of default, `default` -> default,
    `higher` -> twice default, `max` -> Max. Everything is clamped, so "twice as hard"
    is 40 damage rather than 99999, and a bucket can never escape the designer's range
    even when doubling the default would overshoot (Fireball Cooldown: 2 x 1.5 = 3, fine;
    SpeedBoost Multiplier: 2 x 1.6 = 3.2, clamped to the 3.0 Max).
    """
    label = bucket.lower().strip()
    if label not in BUCKET_TO_INDEX:
        raise ValueError(f"unknown bucket {bucket!r}; expected one of {BUCKETS}")

    low = float(param_range["min"])
    high = float(param_range["max"])
    default = float(param_range["default"])

    raw = {
        "much_lower": low,
        "lower": default * 0.5,
        "default": default,
        "higher": default * 2.0,
        "max": high,
    }[label]

    return max(low, min(high, raw))


def resolve_spec(token: dict, buckets: dict[str, str]) -> dict[str, float]:
    """Turn a token's bucket labels into the clamped float spec the game will store.

    Parameters absent from `buckets` take `default`, which is exactly what the C++
    builder does before applying any override.
    """
    resolved: dict[str, float] = {}
    for param_name, param_range in token["ranges"].items():
        bucket = buckets.get(param_name, DEFAULT_BUCKET)
        resolved[param_name] = round(bucket_to_value(param_range, bucket), 4)
    return resolved


def validate_selection(catalogue: dict, token_names: list[str]) -> tuple[bool, str]:
    """Check a token name set against existence and the per-category cap.

    Returns (ok, reason). `reason` is empty when ok. Mirrors ULoadoutValidator so the
    generator can never emit an intent the game would reject.
    """
    known = tokens_by_name(catalogue)
    caps = category_caps(catalogue)

    counts: dict[str, int] = {}
    for name in token_names:
        if name not in known:
            return False, f"unknown token {name!r}"
        category = known[name]["category"]
        counts[category] = counts.get(category, 0) + 1

    if not token_names:
        return False, "empty loadout"

    for category, count in counts.items():
        cap = caps.get(category)
        if cap is not None and count > cap:
            return False, f"{category} cap is {cap}, got {count}"

    return True, ""


def normalise_intent(catalogue: dict, intent: dict) -> dict:
    """Round-trip an intent through the schema, filling in every defaulted parameter.

    Training produces partial labels (only the parameters the phrase actually talks
    about); this widens them to the full spec that the game receives, so evaluation can
    compare one shape against another.
    """
    known = tokens_by_name(catalogue)
    specs = []
    for entry in intent["tokens"]:
        token = known[entry["name"]]
        specs.append(
            {
                "name": entry["name"],
                "params": resolve_spec(token, entry.get("params") or {}),
                "buckets": {
                    param: (entry.get("params") or {}).get(param, DEFAULT_BUCKET)
                    for param in token["ranges"]
                },
            }
        )
    return {"tokens": specs}


def intent_to_json(catalogue: dict, intent: dict) -> str:
    """Stable serialisation, used for dataset rows and for exact-match scoring."""
    normalised = normalise_intent(catalogue, intent)
    payload = [
        {"name": spec["name"], "buckets": spec["buckets"]} for spec in normalised["tokens"]
    ]
    payload.sort(key=lambda item: item["name"])
    return json.dumps(payload, sort_keys=True)


if __name__ == "__main__":
    # Self-check: the mappings the game agrees with, printed from the real catalogue.
    catalogue = load_catalogue()
    tokens = tokens_by_name(catalogue)

    print(f"catalogue tokens   : {', '.join(tokens)}")
    print(f"held-out token     : {catalogue.get('held_out_token')}")
    print(f"parameter vocab    : {', '.join(parameter_vocabulary(catalogue))}")
    print(f"buckets            : {', '.join(BUCKETS)}")

    fireball = tokens["Fireball"]
    print("\nFireball.Damage across the buckets (range 5 -> 100, default 20):")
    for bucket in BUCKETS:
        print(f"  {bucket:<11} -> {bucket_to_value(fireball['ranges']['Damage'], bucket):>6.2f}")

    speed = tokens["SpeedBoost"]
    print("\nSpeedBoost.Multiplier (range 1.05 -> 3.0, default 1.6):")
    for bucket in BUCKETS:
        print(f"  {bucket:<11} -> {bucket_to_value(speed['ranges']['Multiplier'], bucket):>6.2f}")

    for probe in (["Fireball", "Shield"], ["Invisibility", "Shield"], ["Fireball", "Blink"]):
        ok, reason = validate_selection(catalogue, probe)
        print(f"\nselection {probe} -> {'ok' if ok else 'rejected: ' + reason}")
