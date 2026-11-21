"""Deterministic complement rule for exclusion phrases.

`exclude_all_but` and `exclude_without` are 138 of the 327 misses on the shipped 3-token set and
both score ~0.000 for the model *and* for the keyword baseline. The reason is the same in both
paths: they read the sentence forward and equip what it names. "without the fireball" names the
fireball in order to remove it.

The label those families encode is not a metaphor and does not need to be learned: the sentence
names the abilities to EXCLUDE, and the answer is the rest of the candidate set. That is
set subtraction, and set subtraction is the one thing a small transformer is worst at and a
three-line function is perfect at.

It subtracts from the right place, which is the part that took a PIE run to find. The generator
only ever renders "without <the excluded names>", so a rule that treats every name in the
sentence as an exclusion is right on all 143 measured rows and wrong on the first sentence a
player types that asks for something and excludes something else -- "give me a fireball without
the speed boost" equipped Invisibility until `_split_at_marker` started separating the two halves.
See `_split_at_marker` for the two readings.

This module is deliberately not neural and deliberately not in the model's weights. It runs
before the model and only when the phrase carries an exclusion marker, so it cannot move any row
that is not an exclusion request. Everything it emits is still just token names with every
bucket left at `default`; it emits no numbers, and ULoadoutValidator remains the only thing that
turns a label into a gameplay value.

Why `but` needs the extra guard: `param_multi_conflict` renders "punchy but slow fireballs",
where `but` joins two adjectives and excludes nothing. Requiring `all`/`everything` in the same
sentence separates the two readings, and "faster fireballs" carries no marker at all.
"""

from __future__ import annotations

import re

WORD_RE = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def build_aliases(catalogue: dict) -> dict[str, set[str]]:
    """Surface words that name each token, mirroring generate_dataset.build_alias_maps.

    A keyword owned by more than one token is dropped, and so is a category word: `offense`
    selects a category rather than Fireball, and a category is a different label. The token's
    own name always survives, so every token stays nameable.
    """
    owners: dict[str, set[str]] = {}
    for token in catalogue["tokens"]:
        for keyword in token["keywords"]:
            owners.setdefault(keyword.lower(), set()).add(token["name"])

    category_words = {token["category"].lower() for token in catalogue["tokens"]}

    aliases: dict[str, set[str]] = {}
    for token in catalogue["tokens"]:
        pool = {token["name"].lower()}
        display = token.get("display_name") or token["name"]
        pool.add(display.lower())
        for keyword in token["keywords"]:
            lowered = keyword.lower()
            if len(owners[lowered]) > 1 or lowered in category_words:
                continue
            pool.add(lowered)
        aliases[token["name"]] = pool
    return aliases


def mentioned(text: str, catalogue: dict) -> set[str]:
    """Every catalogue token a sentence names, with no candidate filter.

    `apply_exclusion` answers for `candidates` only, so it cannot report that a sentence named
    an ability the shipped set does not carry: it returns None and the phrase falls through to
    the model, which abstains. A measurement then scores the resulting empty set as misses,
    which reads as the shipped path failing a sentence it was never able to express. This is
    the same whole-word scan with no filter, so a caller can tell "read this wrong" from
    "cannot be asked this at all".

    Returns token names, not surface words -- `build_aliases` has already resolved those, and a
    keyword owned by two tokens is dropped there, so an ambiguous word names neither.
    """
    words = _words(text)
    aliases = build_aliases(catalogue)
    return {name for name, pool in aliases.items() if _mentions(words, pool)}


def exclusion_marker(text: str) -> str | None:
    """The marker that makes this an exclusion request, or None."""
    words = _words(text)
    if "without" in words:
        return "without"
    if "except" in words:
        return "except"
    # Bare `but` joins adjectives ("punchy but slow"); it only excludes when the sentence is
    # also quantifying over the whole set ("all abilities but ...", "everything but ...").
    if "but" in words and ("all" in words or "everything" in words):
        return "but"
    if words and words[0] == "no":
        return "no"
    return None


def _mentions(words: list[str], pool: set[str]) -> bool:
    """True if any alias in `pool` appears in `words` as a whole-word sequence."""
    for alias in pool:
        alias_words = _words(alias)
        if not alias_words:
            continue
        span = len(alias_words)
        for start in range(len(words) - span + 1):
            if words[start : start + span] == alias_words:
                return True
    return False


def _split_at_marker(words: list[str], marker: str) -> tuple[list[str], list[str]]:
    """The words before the exclusion marker and the words after it.

    The two halves are not the same instruction, and this is where the rule was wrong:
    "without the fireball" names the fireball in order to remove it, but "give me a fireball
    without the speed boost" names the fireball in order to KEEP it and names the speed boost
    in order to remove it. Reading the whole sentence as one list of names made the second
    sentence drop the very ability it asked for -- it equipped Invisibility, the only token the
    sentence never mentioned.
    """
    try:
        index = words.index(marker)
    except ValueError:
        return words, []
    return words[:index], words[index + 1 :]


def apply_exclusion(text: str, catalogue: dict, candidates: list[str]) -> dict | None:
    """The complement intent for an exclusion phrase, or None to leave the phrase to the model.

    Returns None when there is no exclusion marker, when nothing in the sentence resolves to a
    candidate (so "without breaking the game" is not treated as an exclusion request), or when
    the resulting set would be empty (an empty loadout is not equippable).

    Two readings, chosen by which side of the marker names something:

    * Nothing named before the marker -> the sentence is quantifying over the whole set, so the
      answer is the complement: "without the fireball", "all abilities but the fireball",
      "everything except the fireball", "no fireball" all mean candidates minus the names.
    * Something named before the marker -> the sentence is asking for those and excluding what
      follows: "give me a fireball without the speed boost" means Fireball, not Invisibility.
    """
    marker = exclusion_marker(text)
    if marker is None:
        return None

    words = _words(text)
    aliases = build_aliases(catalogue)
    before, after = _split_at_marker(words, marker)

    kept = {name for name in candidates if name in aliases and _mentions(before, aliases[name])}
    excluded = {name for name in candidates if name in aliases and _mentions(after, aliases[name])}

    if kept:
        remaining = [name for name in candidates if name in kept and name not in excluded]
    else:
        if not excluded:
            return None
        remaining = [name for name in candidates if name not in excluded]

    if not remaining:
        return None

    tokens_by_name = {token["name"]: token for token in catalogue["tokens"]}
    return {
        "tokens": [
            {"name": name, "params": {p: "default" for p in tokens_by_name[name]["ranges"]}}
            for name in remaining
        ]
    }
