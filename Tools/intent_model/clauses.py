"""Deterministic two-clause rule for phrases that move two parameters at once.

`param_multi_agree` is the largest remaining block of error on the shipped 3-token set: 69 of 98
rows missed, and its shape is one sentence naming two parameters:

    shorter cooldowns and heavier fireballs

The failure is not vocabulary. P27 counted the training rows behind every sanctioned
`(parameter, bucket, adjective)` triple and found the pairings present and unstarved; what the
model cannot do is hold two adjective-to-parameter bindings at once and attach both to a noun that
arrives last. P21, P22 and P25 all attacked that in the weights and all lost ground.

So this is the P26 move again, for the same reason it worked there: an exact mapping belongs
outside the weights. The sentence is resolvable without any learning at all, because the label
space is a handful of parameter names and five buckets, and the surface words for both are a
closed list.

Three things make it exact rather than a guess.

**The token is decided by which parameter set the clauses fit**, not by a keyword. Every catalogue
keyword that is also a direction word is dropped before naming is attempted, because
`catalogue.json` lists `fast`, `faster` and `quicker` as SpeedBoost keywords: they appear in 122
SpeedBoost rows and 164 non-SpeedBoost rows, so as presence evidence they are worse than a coin
flip (`SPEED_ADJECTIVES` in generate_dataset.py records the same measurement). "quicker cooldowns
and a bigger multiplier" names no ability at all -- the clauses fit SpeedBoost's parameters, and
that is what identifies it. Where a sentence does name an ability outright, that ability is
preferred and no other is considered.

**Nouns arrive plural.** The family renders `noun_form(name)`, so the corpus says "fireballs", not
"fireball". Whole-word matching against the keyword list therefore found nothing at all; matching
now compares singularised sequences.

**Ambiguity is refused, never guessed.** "fast" on Fireball could be ProjectileSpeed higher or
Cooldown lower; the lexicon orders candidates so the intended reading wins, and a clause carrying
a direction word that resolves to none of the token's parameters refuses the whole phrase and
leaves it to the model. A partial parse is how a loadout loses a parameter it should have kept,
which is the exact defect this rule exists to fix.

It emits bucket labels and token names only. It writes no numbers: `ULoadoutValidator` is still
the only thing that turns a label into a gameplay value.
"""

from __future__ import annotations

import re

from exclusion import _words, build_aliases, exclusion_marker
from schema import DEFAULT_BUCKET

# Same separators the generator's two-clause families use. `but` is here because
# `param_multi_conflict` renders "punchy but slow fireballs"; the exclusion rule owns the other
# reading of `but` ("all abilities but the fireball") and runs first, so the two cannot collide.
CLAUSE_SPLIT = re.compile(r"\s+and\s+|\s*,\s*|\s+but\s+")

# Surface words that name a parameter out loud.
PARAM_WORDS = {
    "damage": "Damage",
    "cooldown": "Cooldown",
    "duration": "Duration",
    "range": "Range",
    "multiplier": "Multiplier",
    "projectile": "ProjectileSpeed",
    "shot": "ProjectileSpeed",
    "distance": "Distance",
    "protection": "DamageReduction",
}

# Direction words that say nothing about which parameter moves, so they only ever apply to a
# parameter the same clause named explicitly.
GENERIC_DIRECTION = {
    "more": "higher",
    "higher": "higher",
    "increased": "higher",
    "increase": "higher",
    "extra": "higher",
    "twice": "higher",
    "less": "lower",
    "lower": "lower",
    "reduced": "lower",
    "reduce": "lower",
    "cheaper": "lower",
    "maximum": "max",
    "max": "max",
    "most": "max",
    "minimum": "much_lower",
    "no": "much_lower",
    "barely": "much_lower",
}

# Bare adjectives, in preference order. The first entry whose parameter the token actually has is
# the reading. Order matters where a word is genuinely two things: "fast" on Fireball is
# ProjectileSpeed (the fireball travels fast) rather than Cooldown, and "slow" on Fireball is
# ProjectileSpeed lower rather than Cooldown higher.
BARE = {
    # damage
    "strong": [("Damage", "higher"), ("Multiplier", "higher")],
    "stronger": [("Damage", "higher"), ("Multiplier", "higher")],
    "punchy": [("Damage", "higher")],
    "punchier": [("Damage", "higher")],
    "hard": [("Damage", "higher")],
    "harder": [("Damage", "higher")],
    "hard-hitting": [("Damage", "higher")],
    "heavy": [("Damage", "higher")],
    "heavier": [("Damage", "higher")],
    "devastating": [("Damage", "max")],
    "brutal": [("Damage", "max")],
    "weak": [("Damage", "lower"), ("Multiplier", "lower"), ("DamageReduction", "lower")],
    "weaker": [("Damage", "lower"), ("Multiplier", "lower"), ("DamageReduction", "lower")],
    "soft": [("Damage", "lower")],
    "softer": [("Damage", "lower")],
    "feeble": [("Damage", "much_lower")],
    # travel speed
    "fast": [("ProjectileSpeed", "higher"), ("Multiplier", "higher"), ("Cooldown", "lower")],
    "faster": [("ProjectileSpeed", "higher"), ("Multiplier", "higher"), ("Cooldown", "lower")],
    "quick": [("ProjectileSpeed", "higher"), ("Multiplier", "higher"), ("Cooldown", "lower")],
    "quicker": [("ProjectileSpeed", "higher"), ("Multiplier", "higher"), ("Cooldown", "lower")],
    "rapid": [("ProjectileSpeed", "higher"), ("Multiplier", "higher"), ("Cooldown", "lower")],
    "slow": [("ProjectileSpeed", "lower"), ("Multiplier", "lower"), ("Cooldown", "higher")],
    "slower": [("ProjectileSpeed", "lower"), ("Multiplier", "lower"), ("Cooldown", "higher")],
    "crawling": [("ProjectileSpeed", "much_lower")],
    "lightning": [("ProjectileSpeed", "max")],
    "blazing": [("ProjectileSpeed", "max")],
    # size / multiplier
    "big": [("Multiplier", "higher"), ("Damage", "higher")],
    "bigger": [("Multiplier", "higher"), ("Damage", "higher")],
    "huge": [("Multiplier", "max")],
    "massive": [("Multiplier", "max")],
    "biggest": [("Multiplier", "max")],
    "small": [("Multiplier", "lower")],
    "smaller": [("Multiplier", "lower")],
    "tiny": [("Multiplier", "much_lower")],
    # duration / range
    "long": [("Duration", "higher"), ("Range", "higher"), ("Cooldown", "higher")],
    "longer": [("Duration", "higher"), ("Range", "higher"), ("Cooldown", "higher")],
    "longest": [("Duration", "max"), ("Range", "max"), ("Cooldown", "max")],
    "lasting": [("Duration", "higher")],
    "enduring": [("Duration", "higher")],
    "short": [("Duration", "lower"), ("Cooldown", "lower"), ("Range", "lower")],
    "shorter": [("Duration", "lower"), ("Cooldown", "lower"), ("Range", "lower")],
    "brief": [("Duration", "much_lower")],
    "briefer": [("Duration", "lower")],
    "endless": [("Duration", "max")],
    "permanent": [("Duration", "max")],
    "forever": [("Duration", "max"), ("Range", "max")],
    "far": [("Range", "higher")],
    "farther": [("Range", "higher")],
    "further": [("Range", "higher")],
    "infinite": [("Range", "max")],
    # damage reduction
    "tough": [("DamageReduction", "higher")],
    "tougher": [("DamageReduction", "higher")],
    "tankier": [("DamageReduction", "higher")],
    "sturdier": [("DamageReduction", "higher")],
    "impenetrable": [("DamageReduction", "max")],
}

ALL_DIRECTION_WORDS = set(BARE) | set(GENERIC_DIRECTION)

# Superlatives and durations that are spelled as several words. Checked before the single-word
# lexicon, because "as long as possible" contains "long" and would otherwise read as `higher`.
# Sorted longest-first at use, so "twice as long" cannot be shadowed.
MULTIWORD = [
    (("as", "long", "as", "possible"), "Duration", "max"),
    (("as", "far", "as", "possible"), "Range", "max"),
    (("as", "much", "as", "possible"), "Damage", "max"),
    (("twice", "as", "long"), "Duration", "higher"),
    (("a", "split", "second"), "Duration", "much_lower"),
    (("across", "the", "whole", "map"), "Range", "max"),
    # "boost" is a SpeedBoost keyword, so a clause built from it names the ability rather than a
    # parameter and the generic direction word has nothing to attach to. These pin it to Multiplier.
    (("more", "of", "a", "boost"), "Multiplier", "higher"),
    (("less", "of", "a", "boost"), "Multiplier", "lower"),
    (("maximum", "speed"), "Multiplier", "max"),
    # "barely any speed" is the generator's `much_lower` phrase for Multiplier, but `barely` alone
    # is only a generic direction word and the bare adjective "fast" (higher) is read first.
    (("barely", "any", "speed"), "Multiplier", "much_lower"),
    (("hardly", "any", "speed"), "Multiplier", "much_lower"),
]

# Multi-word intensity that names NO parameter, so it applies to whichever parameter the clause
# just named. Kept apart from MULTIWORD because every entry there is pinned to one parameter:
# "much lower" is not `Cooldown much_lower`, it is `much_lower` on whatever was spoken.
#
# Corpus v2 is what made this reachable. Before it, `make the {p} on {word} lower` was emitted for
# BOTH `lower` and `much_lower`, so the distinction did not exist in the text and reading the single
# word `lower` was the only honest answer -- that collision was one of the two defects the rebuild
# removed. The phrase now says `much lower` and means it, and the rule was still answering `lower`:
# 12 of the live misses, every one of them on a row the answer key is now certain about.
GENERIC_MULTIWORD = [
    (("much", "lower"), "much_lower"),
    (("much", "less"), "much_lower"),
    (("a", "great", "deal"), "much_lower"),
]


def _singular(word: str) -> str:
    """`fireballs` -> `fireball`, `invisibilities` -> `invisibility`.

    The corpus speaks in plurals because it renders `noun_form(name)`, and `pluralise` turns a
    y-ending display name into `-ies`. Stripping only a trailing `s` left `invisibilities` as
    `invisibilitie`, so no alias matched it and every phrase ending that way lost its token.
    """
    if len(word) > 4 and word.endswith("ies"):
        return f"{word[:-3]}y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _sequence(words: list[str], span: int, start: int) -> list[str]:
    return [_singular(word) for word in words[start : start + span]]


def _named_tokens(text: str, catalogue: dict, candidates: list[str]) -> list[str]:
    """Candidates the sentence names outright, ignoring every keyword that is a direction word.

    `fast`, `faster` and `quicker` are SpeedBoost keywords and also the words a player uses to ask
    for more projectile speed, so they cannot carry presence. Dropping them from the naming pool
    is what stops "stronger and faster fireballs" from resolving against SpeedBoost.
    """
    words = [_singular(word) for word in _words(text)]
    aliases = build_aliases(catalogue)

    named: list[str] = []
    for name in candidates:
        if name not in aliases:
            continue
        for alias in aliases[name]:
            alias_words = _words(alias)
            if not alias_words:
                continue
            if all(word in ALL_DIRECTION_WORDS for word in alias_words):
                continue
            span = len(alias_words)
            alias_words = [_singular(word) for word in alias_words]
            if any(
                _sequence(words, span, start) == alias_words
                for start in range(len(words) - span + 1)
            ):
                named.append(name)
                break
    return named


def _phrase_present(words: list[str], pattern: tuple[str, ...]) -> bool:
    span = len(pattern)
    return any(
        tuple(_sequence(words, span, start)) == pattern
        for start in range(len(words) - span + 1)
    )


def _multiword_matches(words: list[str]) -> list[tuple[str, str]]:
    """Every multi-word direction the clause contains, longest phrase first."""
    for pattern, param, bucket in sorted(MULTIWORD, key=lambda item: -len(item[0])):
        if _phrase_present(words, pattern):
            yield param, bucket


def _generic_multiword(words: list[str]) -> str | None:
    """The bucket a parameter-agnostic multi-word intensity asks for, or None.

    Checked before the single-word lexicon, because `much lower` contains `lower` and the
    single word would otherwise win.
    """
    for pattern, bucket in sorted(GENERIC_MULTIWORD, key=lambda item: -len(item[0])):
        if _phrase_present(words, pattern):
            return bucket
    return None


def _resolve_clause(clause: str, params: list[str]) -> tuple[str, str] | None | bool:
    """`(param, bucket)`, or False when the clause speaks about a direction it cannot place.

    Returning False rather than None is what lets the caller refuse the whole phrase: a clause
    carrying a direction word we could not attach to one of this token's parameters is evidence
    that the sentence is outside this rule's contract, not evidence to ignore.
    """
    words = [_singular(word) for word in _words(clause)]
    if not words:
        return None

    spoken = [PARAM_WORDS[word] for word in words if word in PARAM_WORDS]
    if spoken and not any(param in params for param in spoken):
        # The clause names a parameter this ability does not have, so the phrase is not about
        # this ability. Falling through to the bare adjectives here is how "a bigger multiplier"
        # resolved against Fireball's Damage and made the wrong token look viable.
        return False
    named = [param for param in spoken if param in params]

    if named:
        param = named[0]
        # Explicit parameter: the adjective is read against THIS parameter, never against the
        # token's others. "shorter cooldowns" is Cooldown lower even on a token that also has
        # Duration.
        for candidate_param, bucket in _multiword_matches(words):
            if candidate_param == param:
                return param, bucket
        generic = _generic_multiword(words)
        if generic:
            return param, generic
        for word in words:
            for candidate_param, bucket in BARE.get(word, ()):
                if candidate_param == param:
                    return param, bucket
            if word in GENERIC_DIRECTION:
                return param, GENERIC_DIRECTION[word]
        return False

    for candidate_param, bucket in _multiword_matches(words):
        if candidate_param in params:
            return candidate_param, bucket

    for word in words:
        for candidate_param, bucket in BARE.get(word, ()):
            if candidate_param in params:
                return candidate_param, bucket

    # No parameter named and no adjective we know: a clause that says nothing about numbers
    # ("make the fireball", "give the speed boost") is simply skipped. A parameter-agnostic
    # intensity with no parameter to attach to is the opposite case -- direction without a place
    # -- so it refuses rather than being ignored.
    if _generic_multiword(words):
        return False
    if any(word in ALL_DIRECTION_WORDS for word in words):
        return False
    return None


def _resolve_for(text: str, catalogue: dict, token: str, min_params: int = 2) -> dict | None:
    """The intent this phrase implies for `token`, or None if the phrase does not fit it cleanly."""
    tokens_by_name = {entry["name"]: entry for entry in catalogue["tokens"]}
    params = list(tokens_by_name[token].get("ranges", {}))
    if len(params) < 2:
        return None

    resolved: dict[str, str] = {}
    for clause in CLAUSE_SPLIT.split(text):
        outcome = _resolve_clause(clause, params)
        if outcome is False:
            return None
        if outcome is None:
            continue
        param, bucket = outcome
        if param in resolved and resolved[param] != bucket:
            return None  # the phrase contradicts itself about one parameter
        resolved[param] = bucket

    if len(resolved) < min_params:
        return None

    buckets = {param: DEFAULT_BUCKET for param in params}
    buckets.update(resolved)
    return {"tokens": [{"name": token, "params": buckets}]}


def apply_clauses(text: str, catalogue: dict, candidates: list[str]) -> dict | None:
    """The two-parameter intent for a clause-conjunction phrase, or None to leave it to the model.

    Refuses whenever the evidence is anything less than complete: an exclusion marker (owned by
    `exclusion.apply_exclusion`, which runs first), two abilities named at once, fewer than two
    distinct parameters resolved, or a direction word that attaches to no parameter.
    """
    if exclusion_marker(text) is not None:
        return None

    named = _named_tokens(text, catalogue, candidates)

    # A phrase with no conjunction at all describes a single change ("shoot with the maximum
    # damage"), and then one resolved parameter is complete evidence; with two clauses it is not,
    # because the other clause is still unexplained. This rule belongs to the PHRASE, not to one
    # branch of the naming test, and it used to live in the `len(named) == 1` branch only. The
    # ambiguous branch paid for that: "make the projectile speed on ball of fire much lower" names
    # Fireball ("ball of fire") and SpeedBoost ("speed", which is half of Fireball's
    # ProjectileSpeed parameter name), was then asked for two parameters by a one-clause sentence,
    # failed both candidates, refused, and left the row to the model -- which answered SpeedBoost.
    # Four `param_single_superlative` rows invented that token and nothing else in the residual did.
    single = len(CLAUSE_SPLIT.split(text)) == 1
    min_params = 1 if single else 2

    if len(named) == 1:
        # The sentence says which ability it means; do not consider a different one.
        #
        # Gating this on an explicit parameter word was measured and rejected: it avoided six
        # wrong rows but took eighteen right ones with it (fired 198/185 -> 174/167, shipped
        # intent exact 0.750 -> 0.734). Bare adjectives after a named ability carry real evidence.
        return _resolve_for(text, catalogue, named[0], min_params)
    if len(named) > 1:
        # Several candidates are named, but only one of them can be the subject: the phrases in
        # question are "maximum projectile speed and longer cooldowns fireballs", where `speed`
        # names SpeedBoost while being half of the name of Fireball's ProjectileSpeed parameter.
        # Naming alone cannot separate them, so fall back to the same evidence the unnamed case
        # uses -- the parameter set the clauses fit -- and refuse unless exactly one survives.
        surviving = [
            intent for intent in
            (_resolve_for(text, catalogue, name, min_params) for name in named)
            if intent is not None
        ]
        return surviving[0] if len(surviving) == 1 else None

    # No ability named: the parameter set the clauses fit is what identifies the ability. This is
    # the "more duration and more multiplier" case, where only SpeedBoost has both.
    surviving = [intent for intent in
                 (_resolve_for(text, catalogue, name) for name in candidates)
                 if intent is not None]
    if len(surviving) != 1:
        return None
    return surviving[0]
