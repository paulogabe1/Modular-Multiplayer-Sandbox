"""Audit the intent corpus for rows whose text cannot determine their own label.

A label the sentence does not express is unlearnable noise: the same words are asked to
mean two different things, so no rule and no model can be scored fairly on them. The
generator's own doctrine already says this (see the comment above `param_single`'s
`s.tokens = [name]`), but nothing enforced it, and two defects slipped through:

1. **Unexpressed buckets.** `prepare()` has no branch for ten families, so they fall
   through to

       return sample_factory(rng, mentioned_count=rng.choice([0, 1]))

   and `sample()` turns `mentioned_count=1` into one random (token, parameter) pair driven
   to a bucket drawn from `BUCKET_WEIGHTS`. None of those ten renderers prints an intensity
   word -- each builds its sentence from `s.words[n]` and fixed frame text only -- so when
   the draw lands on a non-default bucket the label asserts something the sentence has no
   way to say. Roughly one live row in five was affected.

2. **One frame, two buckets.** `_param_single_sentence`'s else-branch emitted identical
   wording for `lower` and `much_lower`, so the two labels shared a surface form. Inside a
   single split the generator's own de-duplication hid it; across splits (valid and test
   both phrase through the curated vocabulary) it did not, and the same sentence carried
   two different answers.

The measurement (`measure_clauses.undetermined`) detects both after the fact and reports a
ceiling of 0.858 on corpus v1. This module runs the same two checks BEFORE anything is
written, so a regeneration cannot reintroduce them: `generate_dataset.main()` calls
`find_problems()` and exits without writing a single file if it returns anything.

The check is deliberately stdlib-only. It is a gate inside the generator, so it must not
pull in torch (directly or via `train.py`) just to refuse a bad corpus.

Run from this directory:
    ..\\..\\.venv\\Scripts\\python.exe dataset_audit.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from schema import DEFAULT_BUCKET

HERE = Path(__file__).parent

SPLITS = ("train", "valid", "test", "test_heldout")

# The ambiguity check spans exactly the splits `measure_clauses.build_ambiguity()` scans,
# because that is the definition the reported ceiling is computed from. `test_heldout` is
# excluded: it forces one token into every label by construction, so it is not part of the
# scored set and its texts are generated from a different token set entirely.
AMBIGUITY_SPLITS = ("train", "valid", "test")

# Families `generate_dataset.prepare()` has no branch for. They reach its last line, the
# `rng.choice([0, 1])` fallthrough, and their renderers print aliases and frame text only.
#
# `only_suffix` is deliberately ABSENT: `prepare()` narrows it to `mentioned_count=0` at
# :1059 and rewrites every bucket to `default`, so its labels are honest. A prefix test on
# "only" would have swept it in and flagged 60 correct rows. Keeping this a literal list of
# ten is what makes that distinction expressible -- and it must shrink if `prepare()` ever
# gains a branch for one of them.
BUCKETLESS_FAMILIES = frozenset({
    "allow_conversational",
    "allow_terse",
    "only_prefix",
    "only_verb",
    "exclude_everything_except",
    "exclude_all_but",
    "exclude_no_terse",
    "exclude_without",
    "request_want",
    "request_can_we",
})


def normalise_text(text: str) -> str:
    """The comparison form for a sentence: case and whitespace carry no meaning."""
    return " ".join(text.lower().split())


def label_key(tokens: list[dict]) -> frozenset:
    """A row's label reduced to a comparable identity, same shape the scorer uses."""
    return frozenset(
        (entry["name"], tuple(sorted(entry["params"].items()))) for entry in tokens
    )


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_splits(directory: Path = HERE) -> dict[str, list[dict]]:
    """Read every split that exists, keyed by split name."""
    splits: dict[str, list[dict]] = {}
    for split in SPLITS:
        path = directory / f"{split}.jsonl"
        if path.exists():
            splits[split] = read_jsonl(path)
    return splits


def moved_params(row: dict) -> list[str]:
    """Every (token, parameter, bucket) the label asserts something non-default about."""
    return [
        f"{entry['name']}.{param}={bucket}"
        for entry in row["tokens"]
        for param, bucket in entry["params"].items()
        if bucket != DEFAULT_BUCKET
    ]


def unexpressed_bucket_problems(splits: dict[str, list[dict]]) -> list[str]:
    """Defect 1: a bucketless family claiming a bucket its sentence never prints."""
    problems: list[str] = []
    for split, rows in splits.items():
        for row in rows:
            if row.get("family", "") not in BUCKETLESS_FAMILIES:
                continue
            moved = moved_params(row)
            if moved:
                problems.append(
                    f"{split}: label not in text [{row['family']}]: "
                    f"{row['text']!r} claims {', '.join(moved)}"
                )
    return problems


def ambiguous_texts(splits: dict[str, list[dict]]) -> dict[str, dict]:
    """Defect 2: one surface form carrying two different labels.

    Returns {normalised text: {label key: [splits it appeared in]}} for the texts that
    carry more than one label. `measure_clauses.undetermined` treats exactly these rows as
    unanswerable, so agreeing with it here is what makes the ceiling reach 1.000.
    """
    seen: dict[str, dict] = {}
    for split in AMBIGUITY_SPLITS:
        for row in splits.get(split, ()):
            key = label_key(row["tokens"])
            entry = seen.setdefault(normalise_text(row["text"]), {})
            entry.setdefault(key, []).append(split)
    return {text: labels for text, labels in seen.items() if len(labels) > 1}


def ambiguity_problems(splits: dict[str, list[dict]]) -> list[str]:
    problems: list[str] = []
    for text, labels in sorted(ambiguous_texts(splits).items()):
        where = sorted({split for splits_seen in labels.values() for split in splits_seen})
        answers = " | ".join(
            ", ".join(f"{name}[{dict(params)}]" for name, params in sorted(key))
            for key in labels
        )
        problems.append(
            f"same words, two labels ({'/'.join(where)}): {text!r} -> {answers}"
        )
    return problems


def find_problems(splits: dict[str, list[dict]]) -> list[str]:
    """Every row whose text does not determine its label. Empty means the corpus is sound."""
    return unexpressed_bucket_problems(splits) + ambiguity_problems(splits)


def undetermined_count(splits: dict[str, list[dict]]) -> dict[str, int]:
    """Per-split count of undetermined rows, the quantity the reported ceiling is built from."""
    bad: dict[str, set[int]] = {split: set() for split in splits}

    for split, rows in splits.items():
        for index, row in enumerate(rows):
            if row.get("family", "") in BUCKETLESS_FAMILIES and moved_params(row):
                bad[split].add(index)

    ambiguous = ambiguous_texts(splits)
    for split in AMBIGUITY_SPLITS:
        for index, row in enumerate(splits.get(split, ())):
            if len(ambiguous.get(normalise_text(row["text"]), ())) > 1:
                bad[split].add(index)

    return {split: len(indices) for split, indices in bad.items()}


def census(splits: dict[str, list[dict]]) -> dict[str, dict[str, int]]:
    """Rows per family per split, so a regeneration can be compared with the last one."""
    result: dict[str, dict[str, int]] = {}
    for split, rows in splits.items():
        counts: dict[str, int] = {}
        for row in rows:
            family = row.get("family", "?")
            counts[family] = counts.get(family, 0) + 1
        result[split] = dict(sorted(counts.items()))
    return result


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    directory = Path(argv[0]) if argv else HERE

    splits = load_splits(directory)
    if not splits:
        print(f"no split files found in {directory}")
        return 2

    total = sum(len(rows) for rows in splits.values())
    print(f"corpus     : {directory}")
    print(f"rows       : {total}  across {len(splits)} splits")

    bad = undetermined_count(splits)
    print("\nundetermined rows (label the text cannot determine):")
    for split in SPLITS:
        if split in splits:
            count = bad.get(split, 0)
            share = count / max(1, len(splits[split]))
            print(f"  {split:<13} {count:>5} / {len(splits[split]):>5}  ({share:.3f})")

    print("\nrows per family:")
    for split, counts in census(splits).items():
        print(f"  {split}:")
        for family, count in counts.items():
            flag = "  <- bucketless" if family in BUCKETLESS_FAMILIES else ""
            print(f"    {family:<28} {count:>5}{flag}")

    problems = find_problems(splits)
    unexpressed = [p for p in problems if "label not in text" in p]
    ambiguous = [p for p in problems if "same words, two labels" in p]

    print(f"\nlabel not in text   : {len(unexpressed)}")
    print(f"same words, 2 labels: {len(ambiguous)}")
    for line in (unexpressed[:5] + ambiguous[:5]):
        print(f"  {line}")
    if len(problems) > 10:
        print(f"  ... and {len(problems) - 10} more")

    if problems:
        print("\nFAIL: the corpus contains rows whose text does not determine their label")
        return 1
    print("\nPASS: every label is expressed by its own sentence")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
