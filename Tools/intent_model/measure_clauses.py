"""Measure the deterministic clause rule on the set the game actually ships.

Reports, on the live 3-token subset of `test.jsonl` -- 587 of 1,166 rows on corpus v2:

* the keyword baseline and the P38 checkpoint, for continuity with every earlier number
* the exclusion rule alone, and the clause rule alone: how many rows each fires on and how many
  of those it gets right
* for each rule, that accuracy twice: raw, and on the rows whose text actually determines the
  label. Without the second figure a weak rule and a noisy corpus look identical, and every
  hour spent chasing the difference is spent on rows no rule or model could ever answer
* the composed path twice: what `serve.py` returns (exclusion, then clauses, then the model), and
  what the game actually applies (that, plus the client's own keyword parser on any empty answer).
  `WishCraftPlayerController.cpp` runs that fourth layer, so it is shipped behaviour even though it
  lives outside this directory -- scoring only the service silently understates the game
* both composed figures a second time on the rows the shipped vocabulary can express at all. The
  sentence generator draws ability names from six tokens while the live set carries three, so a
  few rows ask for an ability the game does not have and are unanswerable by anything in here.
  See `unshippable`; the full 588 are still reported above it so every P39/P40 number reproduces

Firing counts matter as much as accuracy here. A rule that fires on 60 rows and is right on 58 is
worth more than one that fires on 200 and is right on 120, because every row a rule answers is a
row the model cannot get wrong.

Run from this directory:
    ..\\..\\.venv\\Scripts\\python.exe measure_clauses.py
"""

from __future__ import annotations

import json
from pathlib import Path

from clauses import apply_clauses
from dataset_audit import BUCKETLESS_FAMILIES
from evaluate import (
    as_label,
    baseline_intent,
    model_intents,
    print_families,
    print_report,
    score,
)
from exclusion import apply_exclusion, exclusion_marker, mentioned
from train import load_checkpoint, read_jsonl

HERE = Path(__file__).parent

# The presence floor. 0.8 was a convention, never a tuned value, and P40 swept it: across
# 0.80 -> 0.30 on the live subset the decode is flat above 0.50 and only the invented-token
# count moves, so 0.50 is the knee. Measured in `Saved/sweep_floor.log`; served by
# `serve.py` as `INTENT_CONFIDENCE`. Keep this equal to the served default or the report
# describes a configuration nothing runs.
CONFIDENCE = 0.50

# Families `generate_dataset.prepare()` has no branch for -- the ones that used to reach its
# last line and pin a bucket on a parameter their sentence never prints. The definition now
# lives in `dataset_audit.BUCKETLESS_FAMILIES` and is imported, not repeated: that module runs
# it as a PRE-WRITE GATE inside the generator, so a second copy here would let the measurement
# and the gate drift apart, and a measurement that has quietly stopped checking anything is
# worse than no measurement at all.
#
# On corpus v2 this is a CANARY, not a correction. `dataset_audit.py` reports 0 undetermined
# rows on all four splits, so `undetermined` below should read 0 / 0.000 and the ceiling should
# read 1.000. A non-zero value means `generate_dataset.py` regressed -- a new family with no
# branch in `prepare()`, or a frame that stopped expressing its bucket -- and the ANSWER KEY is
# broken again, not the model. Do not go looking for a model fix until it is back at zero.
#
# `only_suffix` is deliberately absent: `prepare()` narrows it to `mentioned_count=0` at :1059
# and rewrites every bucket to `default`, so its labels are honest. A prefix test on "only"
# would have swept it in and flagged 60 correct rows.
UNEXPRESSED_BUCKET_FAMILIES = BUCKETLESS_FAMILIES

# Comparative speed adjectives: sanctioned to be absent from the shipped keyword lists, forbidden
# to appear in them. Owner of the contract is `Saved/check_keyword_parity.py` (`BANNED_ALIASES`).
BANNED_ALIASES = {"faster", "quick", "quicker", "rapid", "slow", "slower"}

PROBES = [
    "shorter cooldowns and heavier fireballs",
    "stronger and faster fireballs",
    "make the fireball strong and fast",
    "punchy but slow fireballs",
    "more duration and more multiplier",
    "quicker cooldowns and a bigger multiplier",
    "give the speed boost quicker cooldowns and a bigger multiplier",
    # Shapes that only became reachable once the -ies plural, the `longer cooldowns` reading and
    # the parameter-phrase naming collision were fixed. Each was silent before P30.
    "maximum projectile speed and longer cooldowns fireballs",
    "much longer and the longest cooldowns invisibilities",
    "the longest cooldowns and maximum speed speed boosts",
    "less of a boost and briefer speed boosts",
    "shoot with the maximum damage",
    "give me all abilities but the fireball",
    "without the invisibility",
    "without breaking the game",
]


def keyed(intent: dict | None):
    """An intent reduced to a comparable identity, or None."""
    if intent is None:
        return None
    return frozenset(
        (entry["name"], tuple(sorted(entry["params"].items()))) for entry in intent["tokens"]
    )


def hit(intent: dict | None, row: dict) -> bool:
    return intent is not None and keyed(intent) == keyed({"tokens": row["tokens"]})


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def build_ambiguity() -> dict[str, set]:
    """Surface forms the corpus itself maps to more than one label.

    A sentence that carries two different answers does not determine its answer, so no rule
    and no model can be scored fairly on it. Two mechanisms produce them, and both are
    generator defects rather than model failures:

    1. The families `prepare()` has no branch for -- see `UNEXPRESSED_BUCKET_FAMILIES`. Four of them
       are the exclusion families: `fam_exclude_all_but` / `fam_exclude_without` /
       `fam_exclude_everything_except` / `fam_exclude_no_terse` build the sentence from
       `s.words[n]` for the EXCLUDED tokens only, while `prepare()` falls through to
       `sample(rng, mentioned_count=rng.choice([0, 1]))`. One time in two that pins a random
       non-default bucket on a token -- and prints no word that expresses it. Same words, two
       answers. The other six (`allow_*`, `only_prefix`, `only_verb`, `request_*`) do the same
       thing for the same reason, which is why that mechanism is keyed off a family list rather
       than caught here by text collision.

    2. `generate_dataset.py:659`. The curated frame `make the {p} on {word} lower` is emitted
       for both `lower` and `much_lower`, so six test rows spell the same request twice with
       two different labels.

    `generate_dataset.py`'s own doctrine is that a label the sentence does not express is
    unlearnable noise (see the comment above `param_single`'s `s.tokens = [name]`). These
    rows break that rule; the corpus is frozen for comparability, so they are measured here
    instead of removed.
    """
    seen: dict[str, set] = {}
    for split in ("train", "valid", "test"):
        path = HERE / f"{split}.jsonl"
        if not path.exists():
            continue
        for row in read_jsonl(path):
            seen.setdefault(_norm(row["text"]), set()).add(keyed({"tokens": row["tokens"]}))
    return {text: keys for text, keys in seen.items() if len(keys) > 1}


def undetermined(row: dict, ambiguous: dict[str, set]) -> str:
    """Why this row's label cannot be read off its text, or "" when it can."""
    asserts_a_bucket = any(
        bucket != "default"
        for entry in row["tokens"]
        for bucket in entry["params"].values()
    )
    if asserts_a_bucket and row.get("family", "") in UNEXPRESSED_BUCKET_FAMILIES:
        return "label not in text"
    if len(ambiguous.get(_norm(row["text"]), ())) > 1:
        return "same words, two labels"
    return ""


def unshippable(row: dict, catalogue: dict, names: list[str]) -> list[str]:
    """Catalogue tokens this row's sentence names that the shipped set cannot resolve.

    A row survives the live filter at the top of `main` when its LABEL names only live tokens,
    but that says nothing about its sentence. `fam_exclude_without` and `fam_exclude_all_but`
    draw their excluded names from the full candidate pool, so a few rows render
    "without lightning and shield" for a label that is all three live tokens.
    `apply_exclusion` cannot resolve either name, finds nothing to subtract, returns None, the
    model abstains, and the empty token set is scored as three misses.

    The test has to be narrow, and the first version was not: flagging every row that merely
    NAMES a planned token caught 153 of the 153 live exclusion rows. Naming one in the excluded
    position is harmless and common -- "without lightning and fireball" resolves on `fireball`
    and subtracts it, which is exactly the label -- because the shipped candidate set never
    carried the planned token in the first place. The artefact is only a sentence whose
    exclusion has nothing live to work with: every ability it names is one the game does not
    have, so there is no subtraction to perform.

    Reported as its own subset rather than dropped from the figures above, so every P39/P40
    number still reproduces on the full 588.
    """
    text = row["text"]
    if exclusion_marker(text) is None:
        return []
    named = mentioned(text, catalogue)
    return sorted(named) if not named & set(names) else []


def served_catalogue(full: dict, names: list[str]) -> dict:
    """The catalogue the game actually sends: the live tokens with their full keyword lists.

    `catalogue_live.json` is NOT that file, and assuming it was invalidated every figure in this
    report. It still holds the 5 / 5 / 6 keyword lists P40 replaced in the DataAsset: no `nuke`,
    no `vanish`, no `ghost`, no `turbo`. The client sends the DataAsset, `serve.build_catalogue`
    lets the request override, so those thin lists never reach the model or the rules in play.
    The symptom that found it: the game resolved `nuke stealth sprint` to Fireball + Invisibility
    while a probe built from `catalogue_live.json` returned Invisibility only.

    `Saved/check_keyword_parity.py` owns the contract -- asset keywords == catalogue keywords
    minus BANNED_ALIASES, same order, nothing added -- and pins it against the DataAsset. The
    banned set is repeated here rather than imported because the guard lives outside this
    package, and it is only used to build a measuring stick, never to serve anything. Building
    from `catalogue.json` in code is what stops this drifting a fourth time.
    """
    return {
        **full,
        "tokens": [
            {
                **token,
                "keywords": [
                    word for word in token.get("keywords") or []
                    if word.lower() not in BANNED_ALIASES
                ],
            }
            for token in full["tokens"]
            if token["name"] in names
        ],
    }


def determinate_reading(
    name: str, rows: list[dict], intents: list[dict], ambiguous: dict[str, set]
) -> None:
    """Accuracy over the rows whose text actually determines the label.

    On corpus v1 the ceiling was not 1.000: 76 of the 537 live rows carried a label the sentence
    could not express, so the most any system could score on the whole split was 0.858, and this was
    the reading that said how the composed path did on the answerable part.

    Corpus v2 fixed the generator, so the ceiling is now **1.000** and this figure equals the
    headline. That is exactly why it stays: it is the sharpest canary there is. If these two numbers
    ever diverge again, the answer key has regressed -- see `dataset_audit.py` -- and no model or
    rule change should be spent until `undetermined` is back at zero.
    """
    kept = [
        (intent, row)
        for intent, row in zip(intents, rows)
        if not undetermined(row, ambiguous)
    ]
    right = sum(1 for intent, row in kept if hit(intent, row))
    print(
        f"  {name:9s} text determines the label: {right}/{len(kept)}"
        f"  ({right / max(1, len(kept)):.3f})"
    )


def render(intent: dict | None) -> str:
    if intent is None:
        return "(none)"
    return ", ".join(
        f"{entry['name']}[" + " ".join(f"{p}={b}" for p, b in entry["params"].items()) + "]"
        for entry in intent["tokens"]
    )


def misses(name: str, intents: list, rows: list, ambiguous: dict[str, set]) -> None:
    """Every row a rule answered wrongly: the only rows that can be fixed by changing it."""
    noise = 0
    for intent, row in zip(intents, rows):
        if intent is None or hit(intent, row):
            continue
        family = row.get("family", "?")
        reason = undetermined(row, ambiguous)
        if reason:
            noise += 1
            family = f"{family}  [{reason}]"
        print(f"    MISS [{name}] {row['text']}   ({family})")
        print(f"         got  {render(intent)}")
        print(f"         want {render({'tokens': row['tokens']})}")
    if noise:
        print(
            f"    {noise} of these carry a label the sentence does not determine -- "
            "no rule change can fix them"
        )


def audit(name: str, intents: list, rows: list, ambiguous: dict[str, set]) -> None:
    fired = [(intent, row) for intent, row in zip(intents, rows) if intent is not None]
    right = sum(1 for intent, row in fired if hit(intent, row))
    share = f"{right / len(fired):.3f}" if fired else "n/a"
    print(f"{name:28s} fired {len(fired):4d} rows, right on {right:4d}  ({share})")

    # The number the rule should actually be judged on. A row whose text does not determine
    # its label is wrong for the rule by construction, so counting it against the rule hides
    # the difference between a rule that needs work and a corpus that needs work.
    determinate = [(intent, row) for intent, row in fired if not undetermined(row, ambiguous)]
    if len(determinate) != len(fired):
        right_d = sum(1 for intent, row in determinate if hit(intent, row))
        print(
            f"    text determines the label: {right_d}/{len(determinate)}"
            f"  ({right_d / len(determinate):.3f})"
        )

    families: dict[str, list[int]] = {}
    for intent, row in fired:
        bucket = families.setdefault(row.get("family", "?"), [0, 0])
        bucket[0] += 1
        bucket[1] += 1 if hit(intent, row) else 0
    for family, (count, good) in sorted(families.items(), key=lambda item: -item[1][0]):
        print(f"    {family:28s} {good:3d}/{count:3d}")


def diagnose_shipped(
    rows: list[dict], intents: list[dict], catalogue: dict, ambiguous: dict[str, set]
) -> None:
    """Split the shipped path's wrong rows by what is *actually* wrong.

    `intent exact` is a single number covering two failures with different fixes:

    * a wrong TOKEN SET -- the sentence named abilities the path did not report, so this is a
      presence/detection problem
    * the right set with a wrong BUCKET -- the abilities were found but a level or direction
      was misread, so this is a param problem

    Lumped together they read as one block of work. The split matters because the shipped path
    answers each with different code: the rules own set derivation for the shapes they match,
    the model owns everything else.

    Undetermined rows (see `undetermined`) are labelled inline and totalled separately, so a
    corpus defect is never counted as a bug in the shipped path.
    """
    set_wrong: list[tuple[dict, dict]] = []
    bucket_wrong: list[tuple[dict, dict]] = []
    for row, intent in zip(rows, intents):
        truth_names, truth_buckets = as_label({"tokens": row["tokens"]}, catalogue)
        pred_names, pred_buckets = as_label(intent, catalogue)
        if truth_names != pred_names:
            set_wrong.append((row, intent))
        elif any(
            pred_buckets.get(key, "default") != value
            for key, value in truth_buckets.items()
        ):
            bucket_wrong.append((row, intent))

    total_wrong = len(set_wrong) + len(bucket_wrong)
    print("\nshipped misses, split by what is actually wrong")
    print(f"  wrong token set          : {len(set_wrong):3d}  (presence / detection)")
    print(f"  right set, wrong bucket  : {len(bucket_wrong):3d}  (level / direction)")
    print(f"  total                    : {total_wrong:3d} of {len(rows)}")

    # ---- wrong token set
    if set_wrong:
        by_family: dict[str, list[int]] = {}
        noise = 0
        for row, intent in set_wrong:
            truth = {entry["name"] for entry in row["tokens"]}
            predicted = {entry["name"] for entry in intent["tokens"]}
            missing = sorted(truth - predicted)
            spurious = sorted(predicted - truth)
            reason = undetermined(row, ambiguous)
            noise += 1 if reason else 0
            label = row.get("family", "?") + (f"  [{reason}]" if reason else "")
            bucket = by_family.setdefault(label, [0, 0, 0])
            bucket[0] += 1
            bucket[1] += len(missing)
            bucket[2] += len(spurious)
            print(f"    SET  {row['text']}")
            print(f"         ({label})  missing {missing}  spurious {spurious}")
        print("\n  wrong token set, by family (rows / abilities missed / abilities invented):")
        for label, (count, missed, invented) in sorted(
            by_family.items(), key=lambda item: -item[1][0]
        ):
            print(f"    {label:52s} {count:3d}   {missed:3d} / {invented:3d}")
        if noise:
            print(
                f"    {noise} of these carry a label the sentence does not determine -- "
                "no path change can fix them"
            )

    # ---- right set, wrong buckets
    if bucket_wrong:
        tally: dict[tuple[str, str, str, str], int] = {}
        by_family = {}
        noise = 0
        for row, intent in bucket_wrong:
            _, truth_buckets = as_label({"tokens": row["tokens"]}, catalogue)
            _, pred_buckets = as_label(intent, catalogue)
            reason = undetermined(row, ambiguous)
            noise += 1 if reason else 0
            label = row.get("family", "?") + (f"  [{reason}]" if reason else "")
            by_family[label] = by_family.get(label, 0) + 1
            diffs = []
            for (name, param), truth in truth_buckets.items():
                predicted = pred_buckets.get((name, param), "default")
                if predicted != truth:
                    diffs.append(f"{name}.{param} {truth} -> {predicted}")
                    key = (name, param, truth, predicted)
                    tally[key] = tally.get(key, 0) + 1
            print(f"    BKT  {row['text']}")
            print(f"         ({label})  " + "; ".join(diffs))
        print("\n  wrong buckets, by (ability, parameter, truth -> predicted):")
        for (name, param, truth, predicted), count in sorted(
            tally.items(), key=lambda item: -item[1]
        ):
            print(f"    {name:<14}{param:<18}{truth:>10} -> {predicted:<10} x{count}")
        print("\n  wrong buckets, by family:")
        for label, count in sorted(by_family.items(), key=lambda item: -item[1]):
            print(f"    {label:52s} {count:3d}")
        if noise:
            print(
                f"    {noise} of these carry a label the sentence does not determine -- "
                "no path change can fix them"
            )


def fallback_gain(
    rows: list[dict], intents: list[dict], catalogue: dict, base: dict
) -> None:
    """What the keyword parser would add if the composed path used it as a last resort.

    The shipped path ends at the model. When exclusion, clauses and the model all decline, the
    row produces an empty intent and the client applies nothing -- even though the keyword
    parser already in the client (`URuleParserLibrary::ParseRuleText`) is exact on any row whose
    label is just correctly-named abilities with no parameters, which is precisely the shape a
    confident model declines.

    The measurement is exact rather than an estimate: a fallback can only change rows the
    composed path returned EMPTY, and it cannot unseat a row any earlier layer answered. So the
    only question is how many declined rows the parser gets exactly right, and whether it
    breaks any declined row that was right because its truth was empty too.
    """
    declined = 0
    gain = 0
    harm = 0
    won: dict[str, int] = {}
    for row, intent in zip(rows, intents):
        if intent["tokens"]:
            continue
        declined += 1
        before = hit(intent, row)
        after = hit(baseline_intent(row["text"], catalogue), row)
        if after and not before:
            gain += 1
            family = row.get("family", "?")
            won[family] = won.get(family, 0) + 1
        elif before and not after:
            harm += 1

    projected = base["intent_exact"] + (gain - harm) / max(1, len(rows))
    print("\nkeyword parser as last resort (only on rows the path declined)")
    print(f"  declined rows            : {declined}")
    print(f"  parser exactly right     : {gain}   (would become correct)")
    print(f"  parser would break       : {harm}   (declined rows already correct)")
    print(f"  intent exact now         : {base['intent_exact']:.3f}")
    print(f"  intent exact projected   : {projected:.3f}  ({projected - base['intent_exact']:+.3f})")
    for family, count in sorted(won.items(), key=lambda item: -item[1]):
        print(f"    {family:28s} +{count}")


def apply_client_fallback(
    rows: list[dict], intents: list[dict], catalogue: dict
) -> list[dict]:
    """The fourth answering layer, which does not live in this service.

    `WishCraftPlayerController.cpp` treats an intent whose `tokens` array is empty as "the model
    abstained" and re-answers the phrase with `URuleParserLibrary::ParseRuleText`, the keyword
    parser. It does the same when the service is unreachable or the request cannot start. So the
    game is `exclusion -> clauses -> model -> keywords`, and a measurement that stops at the
    model describes the HTTP service rather than the game a player touches.

    `evaluate.baseline_intent` is the port of that same C++ parser, so substituting it here
    measures the shipped behaviour instead of approximating it. A row the service answered is
    passed through untouched: the client only falls back on an empty list.
    """
    out = []
    for row, intent in zip(rows, intents):
        if intent["tokens"]:
            out.append(intent)
            continue
        keyword = baseline_intent(row["text"], catalogue)
        out.append(keyword if keyword["tokens"] else intent)
    return out


def main() -> None:
    live = json.loads((HERE / "catalogue_live.json").read_text())
    full = json.loads((HERE / "catalogue.json").read_text())
    names = [token["name"] for token in live["tokens"]]

    rows_all = read_jsonl(HERE / "test.jsonl")
    rows = [row for row in rows_all if all(entry["name"] in names for entry in row["tokens"])]
    ambiguous = build_ambiguity()
    reasons: dict[str, int] = {}
    for row in rows:
        why = undetermined(row, ambiguous)
        if why:
            reasons[why] = reasons.get(why, 0) + 1
    undetermined_rows = sum(reasons.values())
    print("live candidates :", ", ".join(names))
    print("test rows       :", f"{len(rows)} of {len(rows_all)} kept")
    print("undetermined    :", f"{undetermined_rows} rows whose text does not determine its label")
    for why, count in sorted(reasons.items(), key=lambda item: -item[1]):
        print(f"                  {count:4d}  {why}")
    print(
        "ceiling         :",
        f"{(len(rows) - undetermined_rows) / len(rows):.3f} intent exact is the most this split"
        " can score, by any system",
    )

    model, vocab = load_checkpoint(str(HERE / "model.pt"))
    baseline = score(rows, [baseline_intent(row["text"], live) for row in rows], live)
    neural_intents = model_intents(model, vocab, live, names, rows, CONFIDENCE)
    neural = score(rows, neural_intents, live)

    exclusion_intents = [apply_exclusion(row["text"], full, names) for row in rows]
    clause_intents = [apply_clauses(row["text"], full, names) for row in rows]

    overlap = sum(
        1 for left, right in zip(exclusion_intents, clause_intents)
        if left is not None and right is not None
    )
    print("overlap         :", overlap, "rows fired by both rules (should be 0)")

    shipped_intents = [
        exclusion if exclusion is not None
        else clause if clause is not None
        else neural_intent
        for exclusion, clause, neural_intent in zip(exclusion_intents, clause_intents, neural_intents)
    ]

    print()
    audit("exclusion rule", exclusion_intents, rows, ambiguous)
    audit("clause rule", clause_intents, rows, ambiguous)
    # Both rules get a miss list. The exclusion rule was the only one without one, and at 0.794 it
    # is the weaker of the two -- an unprinted miss is an unfixable miss.
    misses("exclusion", exclusion_intents, rows, ambiguous)
    misses("clause", clause_intents, rows, ambiguous)
    print()

    print_report("live 3-token -- keyword baseline", baseline)
    print_report("live 3-token -- intent model", neural)
    shipped = score(rows, shipped_intents, live)
    print_report("live 3-token -- service (exclusion + clauses + model)", shipped)
    diagnose_shipped(rows, shipped_intents, live, ambiguous)
    fallback_gain(rows, shipped_intents, live, shipped)

    # What the player actually gets. The service's answer, then the client's own keyword parser on
    # any empty answer. Reported separately because the two numbers answer different questions:
    # the service figure is what a change to this directory can move.
    game_intents = apply_client_fallback(rows, shipped_intents, live)
    game = score(rows, game_intents, live)
    print()
    print_report("live 3-token -- as played (service, then client keyword fallback)", game)
    determinate_reading("service", rows, shipped_intents, ambiguous)
    determinate_reading("as played", rows, game_intents, ambiguous)

    # The numbers above are judged in a 3-token label space but generated by frames that name
    # six tokens, so a handful of sentences ask for an ability the game does not carry. That is
    # an artefact of the measurement, not a defect in the shipped path, and it is scored as its
    # own subset rather than dropped from the figures above -- every P39/P40 number still
    # reproduces exactly, and this section reads as the addition it is.
    blocked = [(index, missing) for index, missing in (
        (index, unshippable(row, full, names)) for index, row in enumerate(rows)
    ) if missing]
    keep = [index for index in range(len(rows)) if index not in {i for i, _ in blocked}]
    print()
    print(
        f"live 3-token, expressible only -- {len(keep)} of {len(rows)} rows"
    )
    print(
        f"  {len(blocked)} rows name an ability the shipped set does not carry, so no answer"
        " from this directory can be right:"
    )
    by_family: dict[str, int] = {}
    for index, missing in blocked:
        key = f"{rows[index].get('family', '?')} names {', '.join(missing)}"
        by_family[key] = by_family.get(key, 0) + 1
    for key, count in sorted(by_family.items()):
        print(f"    {count:3d}  {key}")
    expressible_rows = [rows[index] for index in keep]
    for label, intents in (
        ("service (exclusion + clauses + model)", shipped_intents),
        ("as played (service, then client keyword fallback)", game_intents),
    ):
        subset = [intents[index] for index in keep]
        print_report(
            f"live 3-token expressible -- {label}", score(expressible_rows, subset, live)
        )

    # ---- the vocabulary the client actually sends --------------------------
    # Everything above is measured against `catalogue_live.json`, which turned out to hold the
    # 5/5/6 keyword lists P40 replaced: it predates `nuke`, `vanish`, `ghost`, `turbo`. The client
    # sends the DataAsset, so those thin lists never reach the model or the rules in play, and the
    # figures above understate the game. The PIE run that found it: the game resolved `nuke stealth
    # sprint` to Fireball + Invisibility while a probe built from that file returned Invisibility
    # only. This pass is scored on `served_catalogue`, and it is the block to quote.
    served = served_catalogue(full, names)
    served_neural_intents = model_intents(model, vocab, served, names, rows, CONFIDENCE)
    served_exclusion = [apply_exclusion(row["text"], served, names) for row in rows]
    served_clause = [apply_clauses(row["text"], served, names) for row in rows]
    served_shipped_intents = [
        exclusion if exclusion is not None
        else clause if clause is not None
        else neural_intent
        for exclusion, clause, neural_intent
        in zip(served_exclusion, served_clause, served_neural_intents)
    ]
    served_game_intents = apply_client_fallback(rows, served_shipped_intents, served)
    print()
    print("served vocabulary -- catalogue.json keywords minus the banned comparatives, which is")
    print("what the client sends and therefore what a player is actually scored against")
    audit("served exclusion rule", served_exclusion, rows, ambiguous)
    audit("served clause rule", served_clause, rows, ambiguous)
    print_report(
        "served -- keyword baseline",
        score(rows, [baseline_intent(row["text"], served) for row in rows], served),
    )
    print_report("served -- intent model", score(rows, served_neural_intents, served))
    print_report(
        "served -- service (exclusion + clauses + model)",
        score(rows, served_shipped_intents, served),
    )
    print_report(
        "served -- as played (service, then client keyword fallback)",
        score(rows, served_game_intents, served),
    )
    # The residual on the vocabulary that ships, not the legacy one. This is the list of rows that
    # are actually still wrong in the game.
    diagnose_shipped(rows, served_shipped_intents, served, ambiguous)
    print_report(
        "served expressible -- as played",
        score(expressible_rows, [served_game_intents[index] for index in keep], served),
    )
    # `print_families` names its two score arguments "kw exact" (first) and "mdl ..." (second).
    # The two arguments here are the model alone and the SHIPPED path, so the header says so
    # rather than leaving the reader to guess why `kw exact` reads 0.000 on a family the rules
    # answer -- that 0.000 is the model, and the row beside it is what ships.
    print_families(
        "live 3-token (kw exact = model alone; mdl * = as played)",
        neural,
        game,
    )

    print()
    print("probes (rule intents only):")
    for phrase in PROBES:
        exclusion = apply_exclusion(phrase, full, names)
        clause = apply_clauses(phrase, full, names)
        if exclusion is not None:
            label = "exclusion"
            intent = exclusion
        elif clause is not None:
            label = "clauses"
            intent = clause
        else:
            label = "model (not shown)"
            intent = None
        if intent is None:
            print(f"  {phrase:62s} -> {label}")
            continue
        rendered = ", ".join(
            f"{entry['name']}[" + " ".join(f"{p}={b}" for p, b in entry["params"].items()) + "]"
            for entry in intent["tokens"]
        )
        print(f"  {phrase:62s} -> {label}: {rendered}")


if __name__ == "__main__":
    main()
