"""Synthesise the intent dataset from catalogue.json.

Nothing here names an ability or a parameter: token surface words come from each
token's `keywords` in the catalogue, and parameter surface words are derived from the
parameter's own name by camel-case splitting plus a shared intensity vocabulary. That
is what makes the pipeline vocabulary-agnostic -- add a row to the table and the
generator already knows how to talk about it.

Label space is deliberately small: presence (which tokens) plus one bucket per
parameter. `schema.validate_selection` is consulted on every sample, so a generated
row can never describe a loadout the game would reject.

Splitting is by whole template family, never by example. Random example splits put
near-identical sentences on both sides of the line and report a score that means
nothing; withholding families measures generalisation to phrasing that was not
trained. One token (`held_out_token`) is withheld from training entirely and gets its
own file, which is the measurement of the vocabulary-agnostic claim.

Usage:
    python generate_dataset.py                # default sizes, seed 20261108
    python generate_dataset.py --train 8000 --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from dataset_audit import BUCKETLESS_FAMILIES, find_problems
from schema import (
    BUCKETS,
    DEFAULT_BUCKET,
    load_catalogue,
    param_ranges,
    tokens_by_name,
    validate_selection,
)

OUT_DIR = Path(__file__).parent

# Bucket prior. Heavier on `default` because that is what real phrasing does: most
# rules name abilities and leave the numbers alone. A uniform prior would teach the
# model to invent intensity that nobody asked for.
BUCKET_WEIGHTS = {
    "much_lower": 0.07,
    "lower": 0.13,
    DEFAULT_BUCKET: 0.55,
    "higher": 0.17,
    "max": 0.08,
}

# Shared intensity vocabulary. Applied to any parameter, known or not, which is why a
# brand-new parameter name needs no new templates. `schema.bucket_to_value` gives the
# exact value so the adjectives stay honest: "higher" really is double the default.
GENERIC_INTENSITY = {
    "much_lower": ["way less", "much less", "almost no", "barely any", "hardly any"],
    "lower": ["less", "lower", "reduced", "smaller"],
    DEFAULT_BUCKET: ["normal", "default", "standard", "regular"],
    "higher": ["more", "higher", "increased", "bigger", "extra"],
    "max": ["maximum", "max", "as much as possible", "the most"],
}

# Curated extras for parameters that exist today. Looked up first, generic pool is the
# fallback, so this table is a nicety rather than a dependency.
CURATED_INTENSITY = {
    "Damage": {
        "much_lower": ["barely any damage", "almost no damage"],
        "lower": ["weaker", "softer", "less damage"],
        "higher": ["stronger", "punchier", "harder", "heavier", "more damage"],
        "max": ["maximum damage", "max damage", "devastating"],
    },
    "Cooldown": {
        "much_lower": ["almost no cooldown", "barely any cooldown"],
        "lower": ["shorter cooldowns", "faster cooldowns", "quicker cooldowns"],
        "higher": ["longer cooldowns", "slower cooldowns"],
        "max": ["the longest cooldowns", "maximum cooldown"],
    },
    "Duration": {
        "much_lower": ["almost no duration", "a split second"],
        "lower": ["shorter", "briefer"],
        "higher": ["longer", "twice as long", "much longer"],
        "max": ["as long as possible", "maximum duration", "forever"],
    },
    "Multiplier": {
        "much_lower": ["barely any speed"],
        "lower": ["a smaller boost", "less of a boost"],
        "higher": ["a bigger boost", "more of a boost"],
        "max": ["the biggest boost", "maximum speed"],
    },
    "ProjectileSpeed": {
        "much_lower": ["barely moving"],
        "lower": ["slower projectiles", "slower shots"],
        "higher": ["faster projectiles", "faster shots"],
        "max": ["maximum projectile speed"],
    },
    "Range": {
        "much_lower": ["almost no range"],
        "lower": ["shorter range"],
        "higher": ["longer range", "further"],
        "max": ["maximum range", "across the whole map"],
    },
    "Distance": {
        "much_lower": ["a tiny hop"],
        "lower": ["a shorter hop"],
        "higher": ["further", "a longer hop"],
        "max": ["as far as possible", "maximum distance"],
    },
    "DamageReduction": {
        "much_lower": ["barely any protection"],
        "lower": ["less protection"],
        "higher": ["more protection", "tougher"],
        "max": ["maximum protection", "impenetrable"],
    },
}

# Bare adjectives, one word that can sit in a sentence WITHOUT the parameter name after
# it. CURATED_INTENSITY cannot be reused for that: its entries are phrase-shaped ("faster
# projectiles"), so dropping one into "make the fireball stronger and ___" produced
# "stronger and faster projectiles". That is why the first two-parameter family taught the
# model nothing about "stronger and faster fireballs" -- and why the plain words a player
# types ("strong", "fast") were absent from the corpus entirely. Positive and comparative
# forms are both listed, because both get typed.
CURATED_BARE = {
    "Damage": {
        "much_lower": ["feeble", "much weaker"],
        "lower": ["weaker", "weak"],
        "higher": ["stronger", "strong", "punchier", "harder", "hard-hitting"],
        "max": ["devastating", "brutal"],
    },
    "ProjectileSpeed": {
        "much_lower": ["much slower", "crawling"],
        "lower": ["slower", "slow"],
        "higher": ["faster", "fast", "quicker", "quick", "rapid"],
        "max": ["lightning fast", "blazing fast"],
    },
    "Duration": {
        "much_lower": ["much shorter", "brief"],
        "lower": ["shorter", "short"],
        "higher": ["longer", "long", "lasting", "enduring"],
        "max": ["endless", "permanent"],
    },
    "Cooldown": {
        "much_lower": ["much quicker", "almost instant"],
        "lower": ["quicker", "faster", "fast", "short"],
        "higher": ["slower", "slow", "longer"],
        "max": ["really slow", "glacial"],
    },
    "Multiplier": {
        "much_lower": ["much smaller", "tiny"],
        "lower": ["smaller", "weaker", "weak"],
        "higher": ["bigger", "stronger", "strong"],
        "max": ["huge", "massive"],
    },
    "DamageReduction": {
        "much_lower": ["much weaker", "flimsy"],
        "lower": ["weaker", "weak"],
        "higher": ["tougher", "tankier", "sturdier"],
        "max": ["unstoppable", "impenetrable"],
    },
    "Range": {
        "much_lower": ["much shorter", "point blank"],
        "lower": ["shorter", "short"],
        "higher": ["longer", "long", "farther"],
        "max": ["infinite", "map wide"],
    },
    "Distance": {
        "much_lower": ["much shorter", "tiny"],
        "lower": ["shorter", "short"],
        "higher": ["longer", "long", "farther"],
        "max": ["unlimited", "map wide"],
    },
}

# A speed adjective is the one place in this vocabulary where a bucket word and an ability
# name are the same surface word. "faster" is how a player asks for more ProjectileSpeed on
# the fireball, and it is also listed as a SpeedBoost keyword in the catalogue -- so the two
# readings disagree about PRESENCE, not merely about direction.
#
# Measured on the P19 train split, 1,495 of 7,788 rows contain one of these words and the
# label splits 820 SpeedBoost / 675 not. Per word it is worse than a coin flip for the one
# that matters: "faster" appears in 122 SpeedBoost rows and 164 non-SpeedBoost rows. A word
# present half the time cannot carry presence, so the model fell back to the token text --
# where "faster" literally IS a SpeedBoost keyword -- and equipped SpeedBoost for
# "stronger and faster fireballs".
#
# The fix cannot be to stop saying the word: it is the word players type. The fix is to make
# the NOUN decide, which is what `fam_param_speed_contrast` does.
SPEED_ADJECTIVES = {
    "much_lower": ["much slower", "crawling", "barely moving"],
    "lower": ["slower", "slow"],
    "higher": ["faster", "fast", "quicker", "quick", "rapid"],
    "max": ["blazing fast", "lightning fast", "ridiculously fast"],
}

# Words that are adjectives here, so they can never BE the ability's name inside a frame
# that is already using them as an adjective ("make the faster faster"). NOTE: the family
# below does not consult this set -- it names abilities by display name only -- but the set
# documents which catalogue keywords are adjectives rather than names.
SPEED_ADJECTIVE_WORDS = {"fast", "faster", "quick", "quicker", "rapid", "slow", "slower"}

# Adjective pairs that pull two parameters in opposite directions but both upward.
# This is the family the "punchy but slow fireballs" case needs: no single-keyword
# matcher can express "more damage AND longer cooldown" from one sentence.
CONFLICT_PAIRS = [
    ("punchy", "slow"),
    ("strong", "slow"),
    ("hard-hitting", "sluggish"),
    ("heavy", "slow to recharge"),
    ("devastating", "slow"),
]


# ---------------------------------------------------------------- abstain data


# Phrases in this block have the *shape* of a request and name no ability at all. Until
# P15 the generator had no such rows, so every training sentence mentioned at least one
# candidate and the model learned to read template vocabulary ("make", "give me",
# "reduce", "higher") as evidence about *which* ability was meant. That is why
# "make me a sandwich" came back as SpeedBoost at 1.000 confidence: `make` is in
# hundreds of legitimate rows and nothing had ever taught the model that template words
# alone mean nothing.
#
# Deliberately excluded from both lists: parameter words (damage, cooldown, range,
# duration, multiplier, distance, projectile speed). "make the cooldown lower" with no
# ability named is a genuinely ambiguous request, not a non-request, and labelling it
# "nothing" would teach the model to ignore a real signal.
DISTRACTOR_NOUNS = [
    "music",
    "volume",
    "brightness",
    "gravity",
    "fog",
    "lighting",
    "camera",
    "respawn timer",
    "hud",
    "crosshair",
    "sky",
    "water",
    "floor",
    "walls",
    "score",
    "menu",
    "sun",
    "shadows",
    "texture",
    "sound effects",
    "spawn point",
    "minimap",
    "ambience",
    "field of view",
]

# The same intensity adjectives the parameter families use. Reusing them is the point:
# "the music louder" and "the fireball louder" must not mean different things about the
# catalogue just because one of them happens to name a candidate.
DISTRACTOR_INTENSITY = [
    "higher",
    "lower",
    "faster",
    "slower",
    "stronger",
    "weaker",
    "bigger",
    "smaller",
    "louder",
    "quieter",
    "brighter",
    "darker",
    "heavier",
    "lighter",
    "warmer",
    "cooler",
]

# Off-topic input: no game vocabulary whatsoever. The two phrases the abstain probe
# measures ("make me a sandwich", "what is the weather tomorrow") are held out on
# purpose, so the improvement on them is generalisation rather than memorisation.
OOD_TOPICS = [
    "a pizza",
    "a burrito",
    "a coffee",
    "a taxi",
    "a flight to madrid",
    "a hotel for tonight",
    "an invoice",
    "an email",
    "the laundry",
    "a bigger office",
    "the meeting",
    "my taxes",
    "the wifi",
    "a haircut",
    "a table for two",
    "a new phone",
    "the car insurance",
    "a birthday present",
    "a refund",
    "the report",
    "a train ticket",
    "a drink",
    "some batteries",
    "the newsletter",
    "a lawyer",
    "a couch",
    "a dentist appointment",
    "my prescription",
    "the mortgage",
    "a loan",
    "the plumbing",
    "a locksmith",
    "the recycling",
    "the powerpoint",
    "my luggage",
    "the paperwork",
]

# Families whose correct label is the empty set. `validate_selection` rejects an empty
# loadout -- which is right for the game, since there is nothing to equip -- so these
# rows are exempted from that check in `generate()` and `audit_split()`.
ABSTAIN_FAMILIES = {"abstain_template", "abstain_ood"}


def pretty_param(name: str) -> str:
    """`DamageReduction` -> `damage reduction`. General rule, works for new names."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", name).lower()


def build_alias_maps(catalogue: dict) -> dict[str, list[str]]:
    """Surface words that name a token unambiguously.

    A keyword shared with another token is dropped, so `offense` never renders as a
    name for Fireball and `mobility` never names SpeedBoost -- those words select a
    category, and a category is a different label. The token's own name is always
    included, so every token stays nameable even if its keywords collide.
    """
    keyword_owners: dict[str, set[str]] = {}
    for token in catalogue["tokens"]:
        for keyword in token["keywords"]:
            keyword_owners.setdefault(keyword.lower(), set()).add(token["name"])

    # A category word is not a token name. "defense only" means the Defense category,
    # not Shield, so `defense` must not survive as an alias for any single token.
    category_words = {token["category"].lower() for token in catalogue["tokens"]}

    aliases: dict[str, list[str]] = {}
    for token in catalogue["tokens"]:
        pool = [token["name"]]
        display = token.get("display_name") or token["name"]
        if display != token["name"]:
            pool.append(display)
        for keyword in token["keywords"]:
            lowered = keyword.lower()
            if len(keyword_owners[lowered]) > 1 or lowered in category_words:
                continue
            pool.append(keyword)
        aliases[token["name"]] = pool
    return aliases


def intensity_words(param_name: str, bucket: str) -> str:
    """A surface phrase implying this bucket for this parameter."""
    curated = CURATED_INTENSITY.get(param_name, {})
    if bucket in curated:
        return random.choice(curated[bucket])
    return f"{random.choice(GENERIC_INTENSITY[bucket])} {pretty_param(param_name)}"


def join_natural(words: list[str]) -> str:
    """`[a, b, c]` -> `a, b and c`."""
    if not words:
        return ""
    if len(words) == 1:
        return words[0]
    return f"{', '.join(words[:-1])} and {words[-1]}"


def noun_form(token_name: str) -> str:
    """The token's plural noun, always taken from its display name.

    Keyword aliases mix nouns and verbs (`protect`, `hasty`, `velocity`), so pluralising
    them yields `protects`, `hastys`, `velocitys`. Display names are nouns by
    construction, so "punchy but slow fireballs" is built from the display name while
    the *detection* of the token still comes from keywords.
    """
    token = CURRENT_TOKENS[token_name]
    display = token.get("display_name") or token_name
    return pluralise(display.lower())


def pluralise(word: str) -> str:
    """A correct-enough English plural. Used only on display names."""
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return f"{word}es"
    if len(word) > 1 and word.endswith("y") and word[-2] not in "aeiou":
        return f"{word[:-1]}ies"
    if word.endswith("fe"):
        return f"{word[:-2]}ves"
    if word.endswith("f"):
        return f"{word[:-1]}ves"
    return f"{word}s"


@dataclass
class Sample:
    """One labelled intent plus the surface words chosen to describe it."""

    tokens: list[str]
    buckets: dict[str, dict[str, str]]
    words: dict[str, str]
    family: str = ""
    extra: dict = field(default_factory=dict)

    def names(self) -> str:
        return join_natural([self.words[name] for name in self.tokens])

    def row(self) -> dict:
        return {
            "text": self.extra["text"],
            "tokens": [
                {"name": name, "params": dict(self.buckets[name])}
                for name in self.tokens
            ],
            "family": self.family,
        }


# ---------------------------------------------------------------- sampling


def sample_token_set(
    rng: random.Random, candidates: list[str], forced: list[str]
) -> list[str]:
    """Pick a legal token subset: at least one token, caps respected.

    Absent tokens carry no label at all, so a 1-token label is unambiguous.
    """
    present = list(forced)
    for name in candidates:
        if name in present:
            continue
        if rng.random() < 0.34:
            present.append(name)

    if not present:
        present.append(rng.choice(candidates))

    return enforce_caps(present, candidates)


def enforce_caps(present: list[str], order: list[str]) -> list[str]:
    """Drop tokens past a category cap, keeping the earliest in catalogue order.

    Mirrors ULoadoutValidator: one Defense ability. Deterministic, so a generated row
    has exactly one correct answer rather than several equally-valid ones.
    """
    catalogue = CURRENT_CATALOGUE
    caps = catalogue.get("category_caps") or {"Defense": 1}
    by_name = CURRENT_TOKENS

    counts: dict[str, int] = {}
    kept: list[str] = []
    for name in sorted(present, key=PRIORITY.index):
        category = by_name[name]["category"]
        cap = caps.get(category)
        if cap is not None and counts.get(category, 0) >= cap:
            continue
        counts[category] = counts.get(category, 0) + 1
        kept.append(name)

    kept.sort(key=PRIORITY.index)
    return kept


def sample_buckets(
    rng: random.Random, token_names: list[str], mentioned: dict[str, list[str]]
) -> dict[str, dict[str, str]]:
    """One bucket per parameter of every present token.

    Parameters the phrase does not talk about are labelled `default` -- they still
    contribute loss for present tokens, which is how the model learns that silence
    means "designer default" rather than "something dramatic".
    """
    labels: dict[str, dict[str, str]] = {}
    for name in token_names:
        driven = set(mentioned.get(name, []))
        per_param: dict[str, str] = {}
        for param_name in param_ranges(CURRENT_TOKENS[name]):
            if param_name in driven:
                per_param[param_name] = rng.choices(
                    BUCKETS, weights=[BUCKET_WEIGHTS[b] for b in BUCKETS]
                )[0]
            else:
                per_param[param_name] = DEFAULT_BUCKET
        labels[name] = per_param
    return labels


def pick_words(rng: random.Random, token_names: list[str] | None = None) -> dict[str, str]:
    """A surface word for EVERY candidate, not just the present ones.

    A complement phrase has to be able to name a token that is absent from the label
    ("everything except invisibility"), so words cannot be scoped to the present set.

    A speed adjective is never handed back as a NAME (P39). `fast`, `faster`, `quick`,
    `quicker` and `rapid` are SpeedBoost catalogue keywords AND the bare adjectives for a
    higher ProjectileSpeed / a lower Cooldown, so a name slot that accepts one asserts
    presence with a word that usually means a direction. Measured on corpus v2 that was the
    single largest residual: 62 wrong-token-set rows, most of them `sneak, faster` and
    `how about quicker` where the label legitimately contained SpeedBoost and the model
    dropped it. The adjective still appears in the corpus -- only ever in an adjective
    slot, where `fam_param_speed_contrast` makes the NOUN decide presence. This mirrors
    `_equip_word`, which has filtered the same set since P23; `pick_words` was the last
    name slot that did not.
    """
    return {
        name: rng.choice(
            [w for w in CURRENT_ALIASES[name] if w.lower() not in SPEED_ADJECTIVE_WORDS]
            or CURRENT_ALIASES[name]
        )
        for name in CURRENT_CANDIDATES
    }


def sample(rng: random.Random, mentioned_count: int = 1, forced=()) -> Sample:
    """Draw a legal (tokens, buckets) target and choose words for it."""
    forced_names = list(forced) if forced else list(CURRENT_FORCED)
    candidates = [n for n in CURRENT_CANDIDATES if n not in forced_names]
    names = sample_token_set(rng, candidates, forced_names)

    mentioned: dict[str, list[str]] = {}
    for _ in range(mentioned_count):
        name = rng.choice(names)
        params = list(param_ranges(CURRENT_TOKENS[name]))
        mentioned.setdefault(name, [])
        candidate = rng.choice(params)
        if candidate not in mentioned[name]:
            mentioned[name].append(candidate)

    buckets = sample_buckets(rng, names, mentioned)
    return Sample(tokens=names, buckets=buckets, words=pick_words(rng, names))


# ---------------------------------------------------------------- families
# Each family renders one Sample into a sentence. Families are the unit of splitting.


def fam_allow_conversational(rng, s: Sample) -> str:
    # Singular aliases read fine in an enumeration, and pluralising a verb-ish keyword
    # is what produced "protects and velocitys". Leave them alone.
    return f"{join_natural([s.words[n] for n in s.tokens])} only"


def fam_allow_terse(rng, s: Sample) -> str:
    joiner = rng.choice([", ", " "])
    return joiner.join(s.words[n] for n in s.tokens)


def fam_only_suffix(rng, s: Sample) -> str:
    """Genuinely terse restriction. `only` + a plural label would contradict itself."""
    word = rng.choice([s.words[s.tokens[0]], f"the {s.words[s.tokens[0]]}"])
    tail = rng.choice([
        "only",
        "only please",
        "and nothing else",
        "only, nothing else",
        "just that",
    ])
    lead = rng.choice(["", "just ", "give me ", "i only need "])
    return f"{lead}{word} {tail}"


def fam_only_prefix(rng, s: Sample) -> str:
    return f"only {s.names()}"


def fam_only_verb(rng, s: Sample) -> str:
    return f"{rng.choice(['just give me', 'just let me use', 'i only want'])} {s.names()}"


def fam_exclude_everything_except(rng, s: Sample) -> str:
    excluded = [n for n in CURRENT_CANDIDATES if n not in s.tokens]
    if not excluded:
        return f"everything except {s.words[s.tokens[0]]}"
    return f"everything except {join_natural([s.words[n] for n in excluded])}"


def fam_exclude_all_but(rng, s: Sample) -> str:
    excluded = [n for n in CURRENT_CANDIDATES if n not in s.tokens]
    if not excluded:
        return f"all abilities but {s.words[s.tokens[0]]}"
    return f"all abilities but the {join_natural([s.words[n] for n in excluded])}"


def fam_exclude_no_terse(rng, s: Sample) -> str:
    excluded = [n for n in CURRENT_CANDIDATES if n not in s.tokens]
    if not excluded:
        return f"no {s.words[s.tokens[0]]}"
    return f"no {join_natural([s.words[n] for n in excluded])}"


def fam_exclude_without(rng, s: Sample) -> str:
    excluded = [n for n in CURRENT_CANDIDATES if n not in s.tokens]
    if not excluded:
        return f"without {s.words[s.tokens[0]]}"
    return f"without {join_natural([s.words[n] for n in excluded])}"


def fam_category_only(rng, s: Sample) -> str:
    category = s.extra["category"].lower()
    return rng.choice([
        f"only {category}",
        f"{category} only",
        f"just {category}",
        f"nothing but {category}",
        f"keep it {category} only",
        f"i only want {category}",
        f"{category} abilities only",
    ])


def fam_category_verb(rng, s: Sample) -> str:
    category = s.extra["category"].lower()
    return rng.choice(
        [
            f"give me {category} only",
            f"make it all {category}",
            f"{category} only please",
            f"can we go all {category}",
            f"how about {category} only",
            f"let's do {category} only",
        ]
    )


def _param_single_sentence(rng, s: Sample) -> str:
    """One ability, one parameter moved — the phrasing follows the bucket.

    The bucket is the thing the model has to learn, so it is decided in `prepare()` from
    the bucket prior, never pinned here. Pinning it per family is what broke this: the only
    clean single-parameter direction the training split ever demonstrated was `higher`, so
    the head answered `higher` for every parameter of every phrase (measured p ≈ 0.88) and
    could not reach `max` at all (measured 0.01 even on "maximum damage"). The split now
    varies the *wording* instead of the label: `prepare()` sets `curated_phrasing`, so the
    test and valid splits phrase the same buckets through the curated per-parameter
    vocabulary while training only ever sees the generic pool.
    """
    name = s.extra["param_token"]
    param = s.extra["param"]
    word = s.words[name]
    bucket = s.buckets[name][param]
    p = pretty_param(param)

    # The two splits must be textually disjoint, not merely differently weighted: a test
    # sentence that also appears verbatim in training measures memorisation. So training and
    # evaluation use different syntactic frames AND different adjective pools, and a curated
    # phrase that the generic pool could also produce is filtered out (e.g. "maximum damage"
    # is in both, so eval drops it).
    generic_forms = {f"{w} {p}" for w in GENERIC_INTENSITY[bucket]}

    if s.extra.get("curated_phrasing"):
        pool = [a for a in CURATED_INTENSITY.get(param, {}).get(bucket, []) if a not in generic_forms]
        adjective = rng.choice(pool) if pool else None
        if bucket == "max":
            options = [f"{word} with the maximum {p}"]
        elif bucket == "higher":
            options = [
                f"let me have twice the {p} on {word}",
                f"make the {p} on {word} twice as much",
            ]
        elif bucket == "much_lower":
            # Its own frame, not `lower`'s. The two buckets shared
            # `make the {p} on {word} lower`, so the identical sentence carried two
            # different answers the moment valid and test both phrased through the curated
            # vocabulary. Inside one split the de-duplication hid it, which is why it
            # survived until an audit looked across splits.
            options = [f"make the {p} on {word} much lower"]
        else:
            options = [f"make the {p} on {word} lower"]
        if adjective:
            options.append(f"{adjective} {word}")
    else:
        if bucket == "max":
            options = [f"max {p} {word}", f"give {word} max {p}"]
        elif bucket == "higher":
            options = [f"double the {p} on {word}", f"give {word} twice the {p}"]
        elif bucket == "much_lower":
            # Same reason as the curated branch above. Deliberately not `a lot`, which reads
            # as `higher` on a Cooldown and would blunt the metric instead of the defect.
            options = [
                f"much less {p} on {word}",
                f"lower the {p} on {word} a great deal",
            ]
        else:
            options = [f"less {p} on {word}", f"lower the {p} on {word}"]
        options.append(f"{rng.choice(GENERIC_INTENSITY[bucket])} {p} {word}")
    return rng.choice(options)


# These three names are now historical: they identify the SPLIT (train / test / valid), not
# the bucket. Kept so per-family tables stay comparable with the numbers already published.
def fam_param_single_comparative(rng, s: Sample) -> str:
    return _param_single_sentence(rng, s)


def fam_param_single_superlative(rng, s: Sample) -> str:
    return _param_single_sentence(rng, s)


def fam_param_single_reduce(rng, s: Sample) -> str:
    return _param_single_sentence(rng, s)


def fam_param_single_max(rng, s: Sample) -> str:
    """One ability, one parameter pushed to its ceiling, in the frames players type.

    Train-only, and it exists because of a live failure rather than a metric. In PIE the
    request `make the fireball do maximum damage` moved ProjectileSpeed to its maximum as
    well as Damage. The label shape was fine; the wording was not. `param_single`
    train draws `max` only through the generic pool (`max damage fireball`), so no
    training row ever showed the word `maximum` inside a spoken frame, and no row
    contrasted it with a second parameter that must stay put.

    Forcing the bucket here does NOT repeat the mistake the `param_single` comment warns
    about. That warning was about a *split* demonstrating one direction, which made the
    test family ask for a bucket training had never isolated. `param_single_comparative`
    still draws all four buckets in train; this family only adds volume and phrasing for
    the one bucket that is both rare and the one users reach for first.
    """
    name = s.extra["param_token"]
    param = s.extra["param"]
    p = pretty_param(param)
    word = s.words[name]
    intensity = rng.choice(GENERIC_INTENSITY["max"])

    options = [
        f"make the {word} have {intensity} {p}",
        f"give {word} {intensity} {p}",
        f"the {word} needs {intensity} {p}",
        f"i want {word} with {intensity} {p}",
        f"push the {p} on {word} all the way up",
        f"put the {p} on {word} at the maximum",
    ]
    # `do maximum damage` is the exact shape that failed in play. It only reads
    # naturally for the parameters something is actually dealt or thrown with, so it is
    # gated rather than applied to Duration or Cooldown.
    if param in ("Damage", "ProjectileSpeed"):
        options += [
            f"make the {word} do {intensity} {p}",
            f"make {word} do {intensity} {p}",
        ]
    return rng.choice(options)


def fam_param_multi_conflict(rng, s: Sample) -> str:
    """Two parameters moved at once, at least one upward.

    The curated half is the family the `punchy but slow fireballs` case needs: an
    adjective pair, no parameter name spoken at all. The generic half uses whichever
    two parameters the token happens to have, so a token added later is covered too.
    """
    name = s.extra["param_token"]
    word = s.words[name]
    if s.extra.get("curated"):
        return f"{s.extra['admire']} but {s.extra['detract']} {noun_form(name)}"
    return f"{word} with {s.extra['phrase_a']} but {s.extra['phrase_b']}"


def fam_param_multi_agree(rng, s: Sample) -> str:
    name = s.extra["param_token"]
    first = s.extra["adj_a"]
    second = s.extra["adj_b"]
    return f"{first} and {second} {noun_form(name)}"


def _bare_adjective(rng, param: str, bucket: str) -> str:
    """A one-word adjective for the bucket, safe to join with "and" in a bare frame."""
    pool = CURATED_BARE.get(param, {}).get(bucket)
    if pool:
        return rng.choice(pool)
    return f"{GENERIC_INTENSITY[bucket][0]} {pretty_param(param)}"


def _name_word(token_name: str) -> str:
    """The ability's own name, singular and lowercased. Never an adjective, never a verb.

    `pick_words` legitimately hands back catalogue keywords, and several of Fireball's are
    verbs (`shoot`, `fireballing`, `blast`), so a verb frame built from one reads as "the
    shoot should be quicker". It also hands back `faster` as SpeedBoost's name -- it is a
    keyword -- which inside a frame that already says "faster" gives "make the faster
    faster". This family's entire job is that the NOUN carries presence, so it uses the
    display name and nothing else.
    """
    token = CURRENT_TOKENS[token_name]
    return (token.get("display_name") or token_name).lower()


def _equip_word(rng, token_name: str) -> str:
    """A surface word for the ability that is a NAME, never a speed adjective.

    `pick_words` can return `faster` or `quick` for SpeedBoost, because they are catalogue
    keywords. The equip family teaches "a name and nothing else changes nothing", so a word
    that doubles as an intensity adjective would teach precisely the opposite of its label.
    """
    pool = [
        alias
        for alias in CURRENT_ALIASES.get(token_name, [token_name])
        if alias.lower() not in SPEED_ADJECTIVE_WORDS
    ]
    return rng.choice(pool) if pool else token_name


def _intensity_adjective(rng, param: str, bucket: str) -> str:
    """A word for the bucket on this parameter, curated first, generic as the fallback."""
    pool = CURATED_INTENSITY.get(param, {}).get(bucket)
    if pool:
        return rng.choice(pool)
    return f"{GENERIC_INTENSITY[bucket][0]} {pretty_param(param)}"


def fam_param_multi_together(rng, s: Sample) -> str:
    """Two parameters pushed the SAME way, in one breath.

    Train-only sibling of `param_multi_agree`. Until now the only two-parameter training
    family was `param_multi_conflict`, which by construction moves one parameter up and the
    other down -- so "stronger and faster fireballs", both up and the more natural request,
    had no training row at all. On the live three-token surface that left the test family
    at 0.041 exact with bucket accuracy 0.498, a coin flip: two parameters in one sentence
    was a concept the model had only ever seen split apart.

    Frames are deliberately NOT the test family's `{a} and {b} {noun}` shape. That shape
    stays unseen so the test split keeps measuring generalisation to a new surface rather
    than memorisation of this one.
    """
    name = s.extra["param_token"]
    word = s.words[name]
    first = s.extra["adj_a"]
    second = s.extra["adj_b"]
    p_a = pretty_param(s.extra["param_a"])
    p_b = pretty_param(s.extra["param_b"])

    options = [
        f"make {word} {first} and {second}",
        f"i want {word} that is {first} and {second}",
        f"give me {word} that is {first} and also {second}",
    ]
    # The "both" frames have to name the BUCKET, not just the direction. `raise both the
    # cooldown and the damage on fireball` used to be emitted for `higher`/`higher` AND for
    # `max`/`max` -- identical words, two different answers. The generator's own
    # de-duplication could not see it because the two rows differed only in the capital of
    # one alias; `dataset_audit.py` compares case-insensitively across the whole split and
    # caught three.
    #
    # Mixed buckets (one `higher`, one `max`) simply do not get the "both" frames: there is
    # no single word for the pair, and `adj_a`/`adj_b` above already carry the mixed case.
    bucket_a = s.buckets[name][s.extra["param_a"]]
    bucket_b = s.buckets[name][s.extra["param_b"]]
    if bucket_a == bucket_b:
        if bucket_a == "max":
            options += [
                f"crank both the {p_a} and the {p_b} on {word} to the max",
                f"{word} with maximum {p_a} and maximum {p_b}",
            ]
        elif bucket_a == "higher":
            options += [
                f"raise both the {p_a} and the {p_b} on {word}",
                f"{word} with more {p_a} and more {p_b}",
            ]
        elif bucket_a == "much_lower":
            options += [
                f"cut both the {p_a} and the {p_b} on {word} right down",
                f"{word} with almost no {p_a} and almost no {p_b}",
            ]
        else:
            options += [
                f"cut both the {p_a} and the {p_b} on {word}",
                f"{word} with less {p_a} and less {p_b}",
            ]
    return rng.choice(options)


# Noun surfaces for the speed-contrast family, plural and singular listed separately because the
# frames below inflect ("that are", "the ..."). Widened because the family was out of SURFACE,
# not out of budget: in P23 it requested 643 rows and realized 312, rejecting 38,268 of 38,580
# attempts as duplicates. Widening adds rows at the family's existing weight, so it does not
# divide anyone else's quota -- unlike adding a whole new family, which is the dilution that
# cost `more duration and more multiplier` in P20.
SPEED_NOUNS: dict[str, tuple[list[str], list[str]]] = {
    "Fireball": (["fireballs", "shots", "blasts"], ["fireball", "shot", "blast"]),
    "SpeedBoost": (["speed boosts", "boosts", "sprints"], ["speed boost", "boost", "sprint"]),
}


def fam_param_speed_contrast(rng, s: Sample) -> str:
    """The same speed word beside two different ability nouns, both labelled honestly.

    Train-only, and it exists because of a measured collision rather than a metric.
    `faster` is simultaneously the bare adjective for Fireball's ProjectileSpeed and a
    catalogue keyword for SpeedBoost. On the live surface that made
    `stronger and faster fireballs` equip SpeedBoost, and `make the fireball strong and
    fast` equip nothing.

    Both halves are rendered from ONE distribution on purpose. Teaching only "speed word
    -> Fireball" would fix the failing sentence and break `make the speed boost faster`,
    which works today. Rendering the pair is what makes the noun, rather than the
    adjective, the thing presence keys on.
    """
    plural = s.extra["speed_plural"]
    singular = s.extra["speed_singular"]
    word = s.extra["name_word"]
    adjective = s.extra["speed_adj"]

    if s.extra["speed_side"] == "noun":
        # The adjective sits beside the ability's own noun, which is the shape a player
        # types when they are not naming a parameter at all.
        options = [
            f"{adjective} {plural}",
            f"i want {adjective} {plural}",
            f"give me {adjective} {plural}",
            f"can i get {adjective} {plural}",
            f"{plural} that are {adjective}",
            f"{adjective} {plural} please",
            f"make the {plural} {adjective}",
            f"the {singular} should be {adjective}",
            f"make the {singular} {adjective}",
            f"i want the {singular} to be {adjective}",
        ]
    else:
        # The ability is named and the adjective is the verb's complement.
        options = [
            f"make the {word} {adjective}",
            f"make {word} {adjective}",
            f"the {word} should be {adjective}",
            f"i want the {word} to be {adjective}",
            f"can you make the {word} {adjective}",
            f"the {word} needs to be {adjective}",
            f"make {plural} {adjective}",
        ]
    return rng.choice(options)


def fam_equip_only(rng, s: Sample) -> str:
    """An ability named by itself. Every parameter stays `default`.

    Train-only, and measured before it was written. On the P20 corpus, 5,076 of 7,433 train
    rows name exactly one ability, but only 1,108 of those leave every parameter at
    `default`, and the five `param_*` families contribute ZERO of them: 3,745
    single-ability rows move a parameter, and every one of those says so with an intensity
    word. The plain shape a player types to *equip* something -- "fireball", "stealth" --
    was therefore thin and outnumbered roughly 13 to 1 by rows where naming an ability also
    moves it.

    That is the whole defect. Measured on the shipped P20 service, `fireball` returned
    ProjectileSpeed LOWER and `stealth` returned Cooldown LOWER, while `invisibility`,
    `speed boost` and `cloak` were correct -- an unstable split that means the rule was never
    learned, not merely mis-weighted.
    """
    word = s.extra["equip_word"]
    # No pure `bare word` frame here, deliberately. `allow_terse` holds that exact string in
    # the TEST split, so rendering it in train intersected the two splits on essentially the
    # whole alias pool (36 distinct strings) and would have turned allow_terse into a
    # memorisation check. The framed forms below carry the same label and leave bare names as
    # the honest generalization test -- if bare still fails after this, that is evidence for
    # accepting the overlap on purpose, not a guess made in advance.
    return rng.choice(
        [
            f"{word} please",
            f"the {word}",
            f"i want {word}",
            f"give me {word}",
            f"give me {word} please",
            f"equip {word}",
            f"add {word}",
            f"i'll take {word}",
            f"use {word}",
            f"i want the {word}",
            f"give me the {word}",
            f"put {word} on",
        ]
    )


def fam_request_want(rng, s: Sample) -> str:
    return rng.choice(
        [f"i want {s.names()}", f"give me {s.names()}", f"i'd like {s.names()} please"]
    )


def fam_request_can_we(rng, s: Sample) -> str:
    return rng.choice(
        [f"can we do {s.names()}", f"how about {s.names()}", f"can i have {s.names()}"]
    )


def fam_abstain_template(rng, s: Sample) -> str:
    """A request-shaped sentence that names nothing in the catalogue.

    This is the sharpest negative class: it reuses the exact verbs and intensity
    adjectives real requests use, applied to a thing that is not an ability.
    """
    noun = s.extra["noun"]
    intensity = s.extra["intensity"]
    updown = rng.choice(["up", "down", "back up", "right down"])
    moreless = rng.choice(["more", "less"])
    return rng.choice(
        [
            f"make the {noun} {intensity}",
            f"change the {noun} to be {intensity}",
            f"the {noun} should be {intensity}",
            f"i want the {noun} {intensity}",
            f"can we make the {noun} {intensity}",
            f"set the {noun} to {intensity}",
            f"increase the {noun}",
            f"reduce the {noun}",
            f"turn the {noun} {updown}",
            f"give me {moreless} {noun}",
        ]
    )


def fam_abstain_ood(rng, s: Sample) -> str:
    """Off-topic input: a request frame around something entirely outside the game."""
    topic = s.extra["topic"]
    return rng.choice(
        [
            f"make me {topic}",
            f"can you get me {topic}",
            f"i need {topic}",
            f"where is {topic}",
            f"what about {topic}",
            f"sort out {topic}",
            f"i want {topic}",
            f"find me {topic}",
        ]
    )


# name, kind, split, renderer. Every kind appears in BOTH train and test so the test
# score reflects an unseen surface form rather than an unseen concept.
#
# `abstain` is the deliberate exception: train-only. Its rows carry an empty label, so
# folding them into test.jsonl would move the denominator of every existing metric and
# break comparability with P13/P14 for no gain. The number worth having is the
# false-positive rate on distractors the corpus never contains, which
# `Saved/probe_abstain.py` reports.
FAMILIES = [
    ("allow_conversational", "allow_list", "train", fam_allow_conversational),
    ("allow_terse", "allow_list", "test", fam_allow_terse),
    ("only_suffix", "only", "train", fam_only_suffix),
    ("only_prefix", "only", "test", fam_only_prefix),
    ("only_verb", "only", "valid", fam_only_verb),
    ("exclude_everything_except", "exclude_complement", "train", fam_exclude_everything_except),
    ("exclude_all_but", "exclude_complement", "test", fam_exclude_all_but),
    ("exclude_no_terse", "exclude_negative", "train", fam_exclude_no_terse),
    ("exclude_without", "exclude_negative", "test", fam_exclude_without),
    ("category_only", "category", "train", fam_category_only),
    ("category_verb", "category", "test", fam_category_verb),
    ("equip_only", "equip", "train", fam_equip_only),
    ("param_single_comparative", "param_single", "train", fam_param_single_comparative),
    ("param_single_max", "param_single", "train", fam_param_single_max),
    ("param_single_superlative", "param_single", "test", fam_param_single_superlative),
    ("param_single_reduce", "param_single", "valid", fam_param_single_reduce),
    ("param_multi_conflict", "param_multi", "train", fam_param_multi_conflict),
    ("param_multi_together", "param_multi", "train", fam_param_multi_together),
    ("param_speed_contrast", "param_speed", "train", fam_param_speed_contrast),
    ("param_multi_agree", "param_multi", "test", fam_param_multi_agree),
    ("request_want", "request_verb", "train", fam_request_want),
    ("request_can_we", "request_verb", "test", fam_request_can_we),
    ("abstain_template", "abstain", "train", fam_abstain_template),
    ("abstain_ood", "abstain", "train", fam_abstain_ood),
]


# ---------------------------------------------------------------- family prep


def prepare(family_name: str, rng: random.Random, sample_factory) -> Sample | None:
    """Build the Sample a family needs, or None if this draw cannot be rendered."""
    if family_name in ABSTAIN_FAMILIES:
        # The label is deliberately empty: these phrases name no ability. `Sample` holds
        # one surface word per token, so with no tokens there are no words.
        s = Sample(tokens=[], buckets={}, words={})
        s.extra["noun"] = rng.choice(DISTRACTOR_NOUNS)
        s.extra["intensity"] = rng.choice(DISTRACTOR_INTENSITY)
        s.extra["topic"] = rng.choice(OOD_TOPICS)
        return s

    if family_name == "category_only" or family_name == "category_verb":
        category = rng.choice(["Offense", "Mobility"])
        members = [n for n in CURRENT_CANDIDATES if CURRENT_TOKENS[n]["category"] == category]
        if not members:
            return None
        s = sample_factory(rng, mentioned_count=0, forced=members)
        if not all(m in s.tokens for m in members):
            return None
        # The label is exactly the category's members, so a category word is a
        # well-defined answer rather than an ambiguous one. Defense is excluded
        # because its cap of 1 makes "only defense" have two valid answers.
        s.tokens = enforce_caps(members, CURRENT_CANDIDATES)
        s.words = pick_words(rng, s.tokens)
        s.buckets = {n: {p: DEFAULT_BUCKET for p in param_ranges(CURRENT_TOKENS[n])} for n in s.tokens}
        s.extra["category"] = category
        return s

    if family_name == "only_suffix":
        # Terse restriction is genuinely one ability; a plural label would contradict
        # the word "only", so force a single-token target rather than fake it.
        s = sample_factory(rng, mentioned_count=0)
        s.tokens = [rng.choice(s.tokens)]
        s.words = pick_words(rng, s.tokens)
        s.buckets = {
            n: {p: DEFAULT_BUCKET for p in param_ranges(CURRENT_TOKENS[n])} for n in s.tokens
        }
        return s

    if family_name == "param_single_max":
        # Train-only sibling of `param_single`, with the bucket pinned to the ceiling. It
        # sets the same one-token, one-parameter label, and leaves every other parameter
        # of that token at DEFAULT -- which is the contrast the live failure was missing.
        s = sample_factory(rng, mentioned_count=1)
        name = rng.choice(s.tokens)
        params = list(param_ranges(CURRENT_TOKENS[name]))
        param = rng.choice(params)
        s.tokens = [name]
        s.buckets = {n: {p: DEFAULT_BUCKET for p in params} for n in s.tokens}
        s.buckets[name][param] = "max"
        s.extra["param_token"] = name
        s.extra["param"] = param
        return s

    if family_name.startswith("param_single"):
        s = sample_factory(rng, mentioned_count=1)
        name = rng.choice(s.tokens)
        params = list(param_ranges(CURRENT_TOKENS[name]))
        param = rng.choice(params)
        # Draw the bucket here and do NOT override it by family. Overriding meant each split
        # demonstrated exactly one direction — train only ever produced `higher` — so the
        # test family `param_single_superlative` asked for a bucket no training row showed
        # in isolation. That is an unseen concept, which the file's own rule forbids.
        bucket = rng.choices(
            ["much_lower", "lower", "higher", "max"], weights=[0.15, 0.3, 0.4, 0.15]
        )[0]
        # The sentence names exactly one ability, so the label must contain exactly one:
        # a label that quietly equips a token the phrase never mentions is unlearnable
        # noise, because the same words would have to mean two different things.
        s.tokens = [name]
        s.buckets = {name: {p: DEFAULT_BUCKET for p in params}}
        s.buckets[name][param] = bucket
        s.extra["param_token"] = name
        s.extra["param"] = param
        # Surface-form split: training sees only the generic intensity pool, so the test and
        # valid splits are genuinely unseen wording for a bucket the model has now seen.
        s.extra["curated_phrasing"] = family_name != "param_single_comparative"
        return s

    if family_name == "param_multi_conflict":
        s = sample_factory(rng, mentioned_count=0)
        name = rng.choice(s.tokens)
        params = list(param_ranges(CURRENT_TOKENS[name]))
        if len(params) < 2:
            return None
        s.tokens = [name]
        s.buckets = {name: {p: DEFAULT_BUCKET for p in params}}

        # Half the family speaks in adjectives (no parameter name at all), which is
        # what no single-keyword matcher can express; the rest names parameters so a
        # token with unfamiliar parameters still gets coverage.
        if "Damage" in params and "Cooldown" in params and rng.random() < 0.5:
            first, second = rng.choice(CONFLICT_PAIRS)
            s.buckets[name]["Damage"] = "higher"
            s.buckets[name]["Cooldown"] = "higher"
            s.extra.update(
                param_token=name, admire=first, detract=second, curated=True
            )
            return s

        first_param, second_param = rng.sample(params, 2)
        first_bucket = rng.choice(["higher", "max"])
        second_bucket = rng.choice(["higher", "lower", "much_lower"])
        s.buckets[name][first_param] = first_bucket
        s.buckets[name][second_param] = second_bucket
        s.extra.update(
            param_token=name,
            phrase_a=intensity_words(first_param, first_bucket),
            phrase_b=intensity_words(second_param, second_bucket),
            curated=False,
        )
        return s

    if family_name == "param_multi_agree":
        s = sample_factory(rng, mentioned_count=0)
        name = s.tokens[0]
        params = list(param_ranges(CURRENT_TOKENS[name]))
        if len(params) < 2:
            return None
        s.tokens = [name]
        first_param, second_param = rng.sample(params, 2)
        s.buckets = {name: {p: DEFAULT_BUCKET for p in params}}
        for param in (first_param, second_param):
            s.buckets[name][param] = rng.choices(
                ["higher", "max", "lower"], weights=[0.5, 0.2, 0.3]
            )[0]
        s.extra["param_token"] = name
        s.extra["adj_a"] = rng.choice(
            CURATED_INTENSITY.get(first_param, {}).get(s.buckets[name][first_param])
            or [f"{GENERIC_INTENSITY[s.buckets[name][first_param]][0]} {pretty_param(first_param)}"]
        )
        s.extra["adj_b"] = rng.choice(
            CURATED_INTENSITY.get(second_param, {}).get(s.buckets[name][second_param])
            or [f"{GENERIC_INTENSITY[s.buckets[name][second_param]][0]} {pretty_param(second_param)}"]
        )
        return s

    if family_name == "param_multi_together":
        s = sample_factory(rng, mentioned_count=0)
        name = rng.choice(s.tokens)
        params = list(param_ranges(CURRENT_TOKENS[name]))
        if len(params) < 2:
            return None
        s.tokens = [name]
        s.buckets = {name: {p: DEFAULT_BUCKET for p in params}}
        first_param, second_param = rng.sample(params, 2)
        # Same direction on purpose -- that is the half the conflict family cannot teach.
        # Downward is included because "cheaper and quicker" is as natural a request as
        # "stronger and faster", and the bucket head has to tell the two apart.
        up = rng.random() < 0.7
        direction = ["higher", "max"] if up else ["lower", "much_lower"]
        s.buckets[name][first_param] = rng.choices(direction, weights=[0.75, 0.25])[0]
        s.buckets[name][second_param] = rng.choices(direction, weights=[0.75, 0.25])[0]
        s.extra.update(
            param_token=name,
            param_a=first_param,
            param_b=second_param,
            adj_a=_bare_adjective(rng, first_param, s.buckets[name][first_param]),
            adj_b=_bare_adjective(rng, second_param, s.buckets[name][second_param]),
            together_up=up,
        )
        return s

    if family_name == "param_speed_contrast":
        # Deliberately about TWO specific (token, parameter) pairs, because that is exactly
        # where one word means two things: on Fireball "faster" is ProjectileSpeed, on
        # SpeedBoost it is Multiplier. Both are legal requests with different labels, so
        # rendering the pair is the contrast.
        speed_pairs = [("Fireball", "ProjectileSpeed"), ("SpeedBoost", "Multiplier")]
        speed_pairs = [
            (token, param)
            for token, param in speed_pairs
            if token in CURRENT_CANDIDATES and param in param_ranges(CURRENT_TOKENS[token])
        ]
        if not speed_pairs:
            return None
        name, param = rng.choice(speed_pairs)
        params = list(param_ranges(CURRENT_TOKENS[name]))
        s = sample_factory(rng, mentioned_count=1)
        s.tokens = [name]
        s.buckets = {n: {p: DEFAULT_BUCKET for p in params} for n in s.tokens}
        # Upward dominates because that is what players ask for, but a speed word can also
        # mean "turn it down", and the direction head has to tell those apart.
        #
        # A guard that withheld the downward words on Fireball was tried as P22, to stop
        # `slow` meaning both ProjectileSpeed-down here and Cooldown-up in
        # `param_multi_conflict`'s "punchy but slow". It cost more than it bought: probes
        # 6/13 against P20's 8/13, live intent exact 0.263 against 0.326, and it inverted
        # `make the speed boost faster` to Multiplier LOWER. Rejected. The phrase it was
        # guarding is one already-wrong phrase; the damage was five.
        bucket = rng.choices(
            ["much_lower", "lower", "higher", "max"], weights=[0.05, 0.2, 0.6, 0.15]
        )[0]
        s.buckets[name][param] = bucket
        plural_pool, singular_pool = SPEED_NOUNS.get(
            name, ([noun_form(name)], [_name_word(name)])
        )
        s.extra.update(
            param_token=name,
            param=param,
            name_word=_name_word(name),
            speed_adj=rng.choice(SPEED_ADJECTIVES[bucket]),
            speed_side=rng.choice(["noun", "verb"]),
            speed_plural=rng.choice(plural_pool),
            speed_singular=rng.choice(singular_pool),
        )
        return s

    if family_name == "equip_only":
        # One ability, no intensity word, every parameter default. `mentioned_count=0` is what
        # makes the label honest: no parameter is driven, so `sample_buckets` already returns
        # `default` for all of them and this branch only narrows the token set to one.
        s = sample_factory(rng, mentioned_count=0)
        name = rng.choice(s.tokens)
        params = list(param_ranges(CURRENT_TOKENS[name]))
        s.tokens = [name]
        s.buckets = {name: {p: DEFAULT_BUCKET for p in params}}
        s.extra["equip_word"] = _equip_word(rng, name)
        return s

    # Ten families have no branch above (see `dataset_audit.BUCKETLESS_FAMILIES`), and none
    # of their renderers prints an intensity word: each builds its sentence from `s.words[n]`
    # -- the ability aliases -- and fixed frame text. So `mentioned_count` must be 0 here.
    #
    # It used to be `rng.choice([0, 1])`, and every draw that landed on 1 pinned a bucket
    # drawn from BUCKET_WEIGHTS onto a random (token, parameter) pair that the sentence never
    # mentions. That asserts something the text cannot express: unlearnable by construction,
    # because the same words would have to mean two different things. Measured on corpus v1
    # by `dataset_audit.py`: 918 rows across the four splits, 194 of the 1,166 test rows.
    #
    # The fix belongs here rather than in the renderers. Those families were designed to
    # express *presence*, not intensity; inventing intensity phrasing inside them would be a
    # new family, not a repair, and it would move the surface form the split was seeded on.
    #
    # `sample_token_set` picks the token set independently of `mentioned_count`, so this
    # cannot change which abilities a phrase names -- only the buckets that were the defect.
    if family_name in BUCKETLESS_FAMILIES:
        return sample_factory(rng, mentioned_count=0)

    return sample_factory(rng, mentioned_count=rng.choice([0, 1]))


# ---------------------------------------------------------------- driver

CURRENT_CATALOGUE: dict = {}
CURRENT_TOKENS: dict = {}
CURRENT_ALIASES: dict = {}
CURRENT_CANDIDATES: list[str] = []
PRIORITY: list[str] = []
CURRENT_FORCED: list[str] = []

# Families with a small surface space cannot fill a large quota, and forcing them to
# would only produce near-duplicates. Weighting keeps the totals on target instead of
# letting `only_suffix` (a few hundred possible sentences) dictate the corpus size.
FAMILY_WEIGHTS = {
    "allow_conversational": 1.2,
    "allow_terse": 1.0,
    "only_suffix": 0.5,
    "only_prefix": 1.0,
    "only_verb": 1.0,
    "exclude_everything_except": 1.2,
    "exclude_all_but": 1.0,
    "exclude_no_terse": 1.0,
    "exclude_without": 1.0,
    "category_only": 0.3,
    "category_verb": 0.3,
    # An equip request changes nothing, and the corpus under-taught that by roughly 13 to 1.
    # Measured before adding: 1,108 of 5,076 single-ability rows were all-default and the
    # param_* families supplied none of them. Broad surface (every non-adjective alias x 12
    # frames), so this weight is spent on rows rather than on duplicate rejections.
    "equip_only": 0.8,
    "param_single_comparative": 1.4,
    # Ceiling requests are the first thing a player types and were the thinnest slice of
    # the corpus; the weight is what turns `max` from a rare draw into a covered one.
    "param_single_max": 1.0,
    "param_single_superlative": 1.2,
    "param_single_reduce": 1.2,
    "param_multi_conflict": 1.4,
    # Two parameters agreeing was the one param_multi case with no training coverage; this
    # weight puts it on par with the conflict family it complements.
    "param_multi_together": 1.2,
    # The speed-adjective collision. P21 dropped this to 0.5 AND raised both param_multi_*
    # weights, and the result lost 0.054 of live intent exact against P20 while also losing
    # `make the fireball punchy but slow` outright. Three changes at once, so none of them
    # was learned from. These are back to P20's values; the only difference now is the
    # downward-word guard in the prepare branch.
    "param_speed_contrast": 1.0,
    "param_multi_agree": 1.2,
    "request_want": 1.0,
    "request_can_we": 1.0,
    # 5.3% of training rows, and deliberately lopsided. The two abstain kinds do NOT cost
    # the same: `abstain_ood` ("make me a pizza") shares no vocabulary with a request, so
    # teaching it is cheap and it carries the user-facing case. `abstain_template`
    # ("make the music louder") is deliberately maximally confusable with a real request,
    # which is what makes it effective AND what makes it expensive -- P15 ran it at 6.3%
    # alongside 2.3% OOD and lost 0.064 token-set exact doing it. So the cheap kind gets
    # the volume (3.6%) and the expensive kind is kept to 1.8%.
    "abstain_template": 0.15,
    "abstain_ood": 0.3,
}


def allocate(total: int, families: list) -> dict[str, int]:
    """Split a split's budget across its families by weight."""
    weights = {f[0]: FAMILY_WEIGHTS.get(f[0], 1.0) for f in families}
    denominator = sum(weights.values()) or 1.0
    return {
        name: max(1, round(total * weight / denominator))
        for name, weight in weights.items()
    }


def configure(catalogue: dict, exclude: list[str], forced: list[str] | None = None) -> None:
    global CURRENT_CATALOGUE, CURRENT_TOKENS, CURRENT_ALIASES, CURRENT_CANDIDATES, PRIORITY
    global CURRENT_FORCED
    CURRENT_CATALOGUE = catalogue
    CURRENT_TOKENS = tokens_by_name(catalogue)
    CURRENT_ALIASES = build_alias_maps(catalogue)
    CURRENT_CANDIDATES = [n for n in CURRENT_TOKENS if n not in exclude]
    PRIORITY = list(CURRENT_TOKENS)
    CURRENT_FORCED = list(forced or [])


def generate(rng: random.Random, family: tuple, count: int) -> tuple[list[dict], dict]:
    name, kind, _split, renderer = family
    rows: list[dict] = []
    seen: set[str] = set()
    attempts = 0
    stats = {"attempts": 0, "unrenderable": 0, "illegal": 0, "duplicate": 0}
    while len(rows) < count and attempts < count * 60:
        attempts += 1
        stats["attempts"] += 1
        try:
            s = prepare(name, rng, sample)
        except (IndexError, KeyError):
            stats["unrenderable"] += 1
            continue
        if s is None or (not s.tokens and name not in ABSTAIN_FAMILIES):
            stats["unrenderable"] += 1
            continue

        # A complement sentence is only honest if something is actually excluded.
        if name.startswith("exclude_") and set(s.tokens) >= set(CURRENT_CANDIDATES):
            stats["unrenderable"] += 1
            continue

        # An abstain row's label is the empty set, which `validate_selection` rejects for
        # the good reason that there is nothing to equip. That is the point of the row.
        if name not in ABSTAIN_FAMILIES:
            ok, _reason = validate_selection(CURRENT_CATALOGUE, s.tokens)
            if not ok:
                stats["illegal"] += 1
                continue

        try:
            text = renderer(rng, s)
        except (IndexError, KeyError):
            stats["unrenderable"] += 1
            continue

        text = " ".join(text.split())
        if not text or text in seen:
            stats["duplicate"] += 1
            continue
        seen.add(text)

        s.family = name
        s.extra["text"] = text
        rows.append(s.row())
    return rows, stats


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def audit_split(rows: list[dict], label: str, catalogue: dict, forbidden=()) -> list[str]:
    """Structural checks on a finished split.

    A generator that does not verify its own output is just a random sentence machine.
    These are the invariants that make the evaluation numbers mean anything: the label
    is one the validator would accept, a parameter family names exactly the one ability
    in its label, every parameter carries a real bucket, and no forbidden token leaks.
    """
    known = tokens_by_name(catalogue)
    problems: list[str] = []

    for row in rows:
        names = [entry["name"] for entry in row["tokens"]]
        if row["family"] in ABSTAIN_FAMILIES:
            # Correct answer is "nothing". A label here would mean the row is teaching the
            # model to answer a non-request, so it is checked in the opposite direction.
            if names:
                problems.append(
                    f"{label}: abstain row carries a label: {row['text']!r}"
                )
        else:
            ok, reason = validate_selection(catalogue, names)
            if not ok:
                problems.append(f"{label}: illegal label ({reason}): {row['text']!r}")

        if row["family"].startswith("param_") and len(names) != 1:
            problems.append(
                f"{label}: param family with {len(names)} tokens: {row['text']!r}"
            )

        for entry in row["tokens"]:
            expected = set(param_ranges(known[entry["name"]]))
            for param, bucket in entry["params"].items():
                if param not in expected:
                    problems.append(f"{label}: unknown param {param} on {entry['name']}")
                if bucket not in BUCKETS:
                    problems.append(f"{label}: unknown bucket {bucket}")
            if expected - set(entry["params"]):
                problems.append(
                    f"{label}: {entry['name']} missing params "
                    f"{sorted(expected - set(entry['params']))}"
                )

        for name in forbidden:
            if name in names:
                problems.append(f"{label}: forbidden token {name} present")

    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the intent dataset.")
    parser.add_argument("--train", type=int, default=8000)
    parser.add_argument("--valid", type=int, default=600)
    parser.add_argument("--test", type=int, default=1200)
    parser.add_argument("--heldout", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20261108)
    parser.add_argument(
        "--skip-abstain",
        action="store_true",
        help=(
            "Leave the abstain families out. This is the controlled comparison: without it, "
            "any metric change after adding negatives is confounded with the corpus being "
            "regenerated, because every family re-samples from one seeded stream."
        ),
    )
    args = parser.parse_args()

    # Each split draws from its OWN stream. Until now all of them shared one seeded stream,
    # so adding or removing a TRAINING family changed the test sentences: the abstain work
    # shifted the test set, and the control run silently reproduced the P14 corpus instead
    # of the current one. Every comparison was moving the target it was scored against.
    # A test split has to be invariant to anything done to training.
    rng = random.Random(args.seed)
    valid_rng = random.Random(args.seed + 1)
    test_rng = random.Random(args.seed + 2)
    heldout_rng = random.Random(args.seed + 3)
    random.seed(args.seed)  # intensity_words draws from the module RNG; keep it reproducible
    catalogue = load_catalogue()
    held_out = catalogue.get("held_out_token")

    families = [
        f for f in FAMILIES if not (args.skip_abstain and f[0] in ABSTAIN_FAMILIES)
    ]
    train_families = [f for f in families if f[2] == "train"]
    valid_families = [f for f in families if f[2] == "valid"]
    test_families = [f for f in families if f[2] == "test"]

    # Training and validation never see the held-out token, not even as a negative.
    configure(catalogue, exclude=[held_out])

    train_budget = allocate(args.train, train_families)
    valid_budget = allocate(args.valid, valid_families)
    test_budget = allocate(args.test, test_families)

    train_rows: list[dict] = []
    diagnostics: list[str] = []
    for family in train_families:
        target = train_budget[family[0]]
        rows, stats = generate(rng, family, target)
        train_rows.extend(rows)
        if len(rows) < target:
            diagnostics.append(
                f"{family[0]:<28} wanted {target:>4}, got {len(rows):>4} "
                f"(attempts {stats['attempts']}, dup {stats['duplicate']}, "
                f"unrenderable {stats['unrenderable']}, illegal {stats['illegal']})"
            )

    valid_rows: list[dict] = []
    random.seed(args.seed + 1)  # intensity_words shares the module RNG; isolate it per split
    for family in valid_families:
        rows, _stats = generate(valid_rng, family, valid_budget[family[0]])
        valid_rows.extend(rows)

    test_rows: list[dict] = []
    random.seed(args.seed + 2)
    for family in test_families:
        target = test_budget[family[0]]
        rows, stats = generate(test_rng, family, target)
        test_rows.extend(rows)
        if len(rows) < target:
            diagnostics.append(
                f"{family[0]:<28} wanted {target:>4}, got {len(rows):>4} "
                f"(attempts {stats['attempts']}, dup {stats['duplicate']}, "
                f"unrenderable {stats['unrenderable']}, illegal {stats['illegal']})"
            )

    # The held-out split puts the token back in play AND forces it into every label, so
    # each row measures exactly one thing: can the model detect a token it never trained
    # on? Only families that name every present token are used -- a parameter family can
    # carry a token in its label without ever mentioning it, which would score as a miss
    # by construction and understate the result.
    configure(catalogue, exclude=[], forced=[held_out])
    heldout_families = [f for f in FAMILIES if f[1] in ("allow_list", "only", "request_verb")]
    heldout_budget = allocate(args.heldout, heldout_families)
    heldout_rows: list[dict] = []
    random.seed(args.seed + 3)
    for family in heldout_families:
        rows, _stats = generate(heldout_rng, family, heldout_budget[family[0]])
        heldout_rows.extend(rows)

    heldout_rows = [row for row in heldout_rows if any(t["name"] == held_out for t in row["tokens"])]

    rng.shuffle(train_rows)
    valid_rng.shuffle(valid_rows)
    test_rng.shuffle(test_rows)
    heldout_rng.shuffle(heldout_rows)

    print(f"held-out token : {held_out} (absent from train/valid/test)")
    print(f"train          : {len(train_rows):>5}  families: {', '.join(f[0] for f in train_families)}")
    print(f"valid          : {len(valid_rows):>5}  families: {', '.join(f[0] for f in valid_families)}")
    print(f"test           : {len(test_rows):>5}  families: {', '.join(f[0] for f in test_families)}")
    print(f"test_heldout   : {len(heldout_rows):>5}  rows mentioning {held_out}")

    kinds_train = sorted({f[1] for f in train_families})
    kinds_test = sorted({f[1] for f in test_families})
    print(f"\nkinds in train : {', '.join(kinds_train)}")
    print(f"kinds in test  : {', '.join(kinds_test)}")
    missing = [k for k in kinds_train if k not in kinds_test]
    print(f"kinds in train but not test: {missing or 'none'}")

    if diagnostics:
        print("\nfamilies that could not reach their target:")
        for line in diagnostics:
            print(f"  {line}")

    # ---- self-audit. Nothing is trusted until it has been checked, and nothing reaches
    # disk until it has been checked. The write used to happen first, which made these
    # checks a report nobody reads: a corpus that failed them was already the corpus on
    # disk, and the next regeneration inherited the defect silently.
    corpus = {
        "train": train_rows,
        "valid": valid_rows,
        "test": test_rows,
        "test_heldout": heldout_rows,
    }
    problems = []
    # The two textual defects: a label the sentence cannot express, and one sentence
    # carrying two answers. `dataset_audit` owns the definition; `-` keeps this file's own
    # structural checks below it in the same list so both block the write.
    problems += find_problems(corpus)
    problems += audit_split(train_rows, "train", catalogue, forbidden=[held_out])
    problems += audit_split(valid_rows, "valid", catalogue, forbidden=[held_out])
    problems += audit_split(test_rows, "test", catalogue, forbidden=[held_out])
    problems += audit_split(heldout_rows, "test_heldout", catalogue)

    train_texts = {row["text"] for row in train_rows}
    leaked = train_texts & {row["text"] for row in test_rows}
    shared_valid = train_texts & {row["text"] for row in valid_rows}

    print(f"\naudit problems : {len(problems)}")
    for line in problems[:5]:
        print(f"  {line}")
    print(f"train/test text overlap   : {len(leaked)}")
    print(f"train/valid text overlap  : {len(shared_valid)}")
    if problems:
        raise SystemExit(
            "dataset failed its own audit -- nothing was written, the previous corpus is "
            "still on disk and still the one the model was measured against"
        )

    write_jsonl(OUT_DIR / "train.jsonl", train_rows)
    write_jsonl(OUT_DIR / "valid.jsonl", valid_rows)
    write_jsonl(OUT_DIR / "test.jsonl", test_rows)
    write_jsonl(OUT_DIR / "test_heldout.jsonl", heldout_rows)
    print(f"\nwrote {sum(len(rows) for rows in corpus.values())} rows to {OUT_DIR}")


if __name__ == "__main__":
    main()
