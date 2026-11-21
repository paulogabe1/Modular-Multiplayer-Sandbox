"""Score the trained model against the shipped keyword parser.

Four questions, in the order they matter:

1. Does it beat the keyword baseline on phrasing it never saw? (`golden.jsonl`)
2. Does it generalise beyond the template families it trained on? (`test.jsonl`, split
   by whole family, so no near-duplicate sentences leak across the line)
3. Where exactly does it go wrong? (per-parameter confusion matrix -- this is what
   step 7 of the plan feeds back into the generator)
4. Can it detect an ability that was never in its training set? (`test_heldout.jsonl`)

The baseline is a faithful port of `URuleParserLibrary::ParseRuleText`: lowercase,
strip spaces/hyphens/underscores, then substring-match each token's name or any of its
keywords. It has no negation and no parameters, so it always answers all-default.
Scoring a weaker baseline than the one actually shipped would make this report a lie.

Usage:
    python evaluate.py                       # uses model.pt
    python evaluate.py --checkpoint model.pt --confidence 0.5
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import torch

from schema import (
    BUCKETS,
    DEFAULT_BUCKET,
    DEFAULT_INDEX,
    MOVED_BUCKETS,
    MOVED_INDICES,
    load_catalogue,
    param_ranges,
    tokens_by_name,
)
from train import Tensors, load_checkpoint, read_jsonl

HERE = Path(__file__).parent


# ---------------------------------------------------------------- baseline


def _normalise(text: str) -> str:
    """Mirror of the C++ Normalize(): lower, then drop space, hyphen and underscore."""
    return text.lower().replace(" ", "").replace("-", "").replace("_", "")


def baseline_intent(text: str, catalogue: dict) -> dict:
    """The keyword parser as it exists today: containment, no negation, no parameters."""
    haystack = _normalise(text)
    matched = []
    for token in catalogue["tokens"]:
        candidates = [token["name"], *(token.get("keywords") or [])]
        for candidate in candidates:
            needle = _normalise(candidate)
            if needle and needle in haystack:
                matched.append(token["name"])
                break

    known = tokens_by_name(catalogue)
    return {
        "tokens": [
            {"name": name, "params": {p: "default" for p in param_ranges(known[name])}}
            for name in matched
        ]
    }


# ---------------------------------------------------------------- model scoring


@torch.no_grad()
def model_intents(
    model,
    vocab,
    catalogue: dict,
    token_names: list[str],
    rows: list[dict],
    confidence: float,
) -> list[dict]:
    """One forward pass for the whole split, expanded into per-row intents."""
    tensors = Tensors(catalogue, token_names, vocab)
    phrase_ids = torch.tensor([vocab.encode(row["text"]) for row in rows], dtype=torch.long)

    phrase, states, pad_mask = model.phrase_encoder(phrase_ids, return_states=True)
    token_vectors = model.text_encoder(tensors.token_ids)
    probabilities = torch.sigmoid(model.presence_logits(phrase, token_vectors))

    param_vectors = model.text_encoder(
        tensors.param_ids.view(-1, tensors.param_ids.size(-1))
    ).view(1, tensors.param_ids.size(0), tensors.param_slots, -1)

    count, candidates = probabilities.shape
    slots = tensors.param_slots
    phrase_ex = phrase.unsqueeze(1).unsqueeze(2).expand(count, candidates, slots, -1)
    # The context depends on (row, candidate, parameter) and nothing else, so each
    # parameter slot is attended separately. Expanding the key/value sequence across the
    # whole (rows x candidates x slots) grid instead costs ~170 MB at 1,166 rows for no
    # arithmetic gain -- the same matmuls, 12x the memory.
    param_grid = param_vectors.expand(count, candidates, slots, -1)
    context_by_slot = [
        model.attend(
            states.unsqueeze(1).expand(count, candidates, -1, -1).reshape(
                count * candidates, states.size(1), -1
            ),
            pad_mask.unsqueeze(1)
            .expand(count, candidates, -1)
            .reshape(count * candidates, -1),
            param_grid[:, :, slot].reshape(count * candidates, -1),
        )
        for slot in range(slots)
    ]
    context = torch.stack(context_by_slot, dim=1).view(count, candidates, slots, -1)
    token_ex = token_vectors.unsqueeze(0).unsqueeze(2).expand(count, candidates, slots, -1)
    param_ex = param_vectors.expand(count, candidates, slots, -1)

    flat = count * candidates * slots
    labels = (
        model.bucket_probs(
            phrase_ex.reshape(flat, -1),
            context.reshape(flat, -1),
            token_ex.reshape(flat, -1),
            param_ex.reshape(flat, -1),
        )
        .argmax(-1)
        .view(count, candidates, slots)
    )

    intents = []
    for row_i in range(count):
        tokens = []
        for token_i, name in enumerate(token_names):
            if probabilities[row_i, token_i].item() < confidence:
                continue
            params = {
                param_name: BUCKETS[labels[row_i, token_i, slot].item()]
                for slot, param_name in enumerate(tensors.params[token_i])
            }
            tokens.append({"name": name, "params": params})
        intents.append({"tokens": tokens})
    return intents


def print_bucket_heads(label: str, heads: dict) -> None:
    print(f"\n{label} -- bucket head, split by question")
    print(f"  rows                 : {heads['rows']}")
    print(
        f"  moved  (did it move?) : {heads['moved_accuracy']:.3f}"
        f"   (always-default scores {1 - heads['moved_base_rate']:.3f})"
    )
    print(
        f"  direction (which way?): {heads['direction_accuracy']:.3f}"
        f"   on {heads['direction_labels']} moved labels"
        f"   (always-most-common scores {heads['direction_majority']:.3f})"
    )
    wrong = [r for r in heads["direction_confusion"] if r["truth"] != r["predicted"]]
    wrong.sort(key=lambda r: (-r["count"], r["parameter"]))
    for record in wrong[:12]:
        print(
            f"    {record['parameter']:<16} truth {record['truth']:<11} -> "
            f"predicted {record['predicted']:<11} x{record['count']}"
        )


@torch.no_grad()
def bucket_head_report(
    model,
    vocab,
    catalogue: dict,
    token_names: list[str],
    rows: list[dict],
) -> dict:
    """The two bucket questions measured separately, on a representative split.

    `score()` reports a single bucket accuracy, and that number is dominated by `default`:
    about 85% of labels are `default`, so a head that answers `default` unconditionally
    scores ~0.85 while having learned nothing about direction. Factorising the head makes
    the two questions measurable on their own, and this is what says whether direction was
    learned at all -- and how far above the always-most-common rate.

    Deliberately not measured on `valid.jsonl`. That split is the reduce-only families, so
    its moved labels are ~85% `lower`; a model trained on the full mix scores near zero
    there for reasons that have nothing to do with skill. An earlier measurement on that
    split produced exactly that false conclusion.
    """
    tensors = Tensors(catalogue, token_names, vocab)
    phrase_ids = torch.tensor([vocab.encode(row["text"]) for row in rows], dtype=torch.long)
    phrase, states, pad_mask = model.phrase_encoder(phrase_ids, return_states=True)
    token_vectors = model.text_encoder(tensors.token_ids)
    param_vectors = model.text_encoder(
        tensors.param_ids.view(-1, tensors.param_ids.size(-1))
    ).view(tensors.param_ids.size(0), tensors.param_slots, -1)

    moved_correct = 0
    moved_total = 0
    moved_true = 0
    direction_correct = 0
    direction_total = 0
    direction_truth: Counter = Counter()
    confusion: dict[str, Counter] = defaultdict(Counter)

    for row_i, row in enumerate(rows):
        truth = {entry["name"]: entry["params"] for entry in row["tokens"]}
        for token_i, name in enumerate(token_names):
            if name not in truth:
                continue
            slots = len(tensors.params[token_i])
            context = model.attend(
                states[row_i].expand(slots, -1, -1),
                pad_mask[row_i].expand(slots, -1),
                param_vectors[token_i, :slots],
            )
            probs = model.bucket_probs(
                phrase[row_i].expand(slots, -1),
                context,
                token_vectors[token_i].expand(slots, -1),
                param_vectors[token_i, :slots],
            )
            for slot, param_name in enumerate(tensors.params[token_i]):
                truth_bucket = truth[name].get(param_name, DEFAULT_BUCKET)
                truth_moved = truth_bucket != DEFAULT_BUCKET
                moved_probability = 1.0 - probs[slot, DEFAULT_INDEX].item()
                moved_total += 1
                moved_true += int(truth_moved)
                moved_correct += int((moved_probability >= 0.5) == truth_moved)
                if not truth_moved:
                    continue
                predicted = MOVED_BUCKETS[int(probs[slot, list(MOVED_INDICES)].argmax())]
                direction_total += 1
                direction_truth[truth_bucket] += 1
                direction_correct += int(predicted == truth_bucket)
                confusion[param_name][(truth_bucket, predicted)] += 1

    majority = direction_truth.most_common(1)[0][1] if direction_truth else 0
    return {
        "rows": len(rows),
        "moved_accuracy": moved_correct / max(1, moved_total),
        "moved_base_rate": moved_true / max(1, moved_total),
        "direction_accuracy": direction_correct / max(1, direction_total),
        "direction_labels": direction_total,
        "direction_majority": majority / max(1, direction_total),
        "direction_confusion": [
            {"parameter": param, "truth": truth_bucket, "predicted": predicted, "count": count}
            for param, counter in confusion.items()
            for (truth_bucket, predicted), count in counter.items()
        ],
    }


# ---------------------------------------------------------------- comparison


def as_label(intent: dict, catalogue: dict) -> tuple[frozenset, dict]:
    """Reduce an intent to (token set, {(token, param): bucket}) for comparison."""
    known = tokens_by_name(catalogue)
    names = frozenset(entry["name"] for entry in intent["tokens"])
    buckets = {}
    for entry in intent["tokens"]:
        ranges = param_ranges(known[entry["name"]])
        for param_name in ranges:
            buckets[(entry["name"], param_name)] = entry["params"].get(param_name, "default")
    return names, buckets


def score(rows: list[dict], intents: list[dict], catalogue: dict) -> dict:
    """Token-set exact, intent exact, and bucket accuracy split by whether the set was right.

    Bucket accuracy measured across every row is not interpretable on its own: a row whose
    token set is wrong drags its buckets in regardless of whether the bucket head was right
    about the ability it DID name. `bucket_accuracy_on_token_match` is the number that says
    whether the bucket head works, separated from whether presence works.
    """
    token_hits = 0
    intent_hits = 0
    bucket_correct = 0
    bucket_total = 0
    matched_correct = 0
    matched_total = 0
    confusion: dict[str, Counter] = defaultdict(Counter)
    per_family: dict[str, dict] = defaultdict(
        lambda: {
            "rows": 0,
            "token_hits": 0,
            "intent_hits": 0,
            "bucket_correct": 0,
            "bucket_total": 0,
            "matched_correct": 0,
            "matched_total": 0,
        }
    )

    for row, intent in zip(rows, intents):
        truth_names, truth_buckets = as_label(row, catalogue)
        pred_names, pred_buckets = as_label(intent, catalogue)

        token_match = truth_names == pred_names
        token_hits += token_match

        row_correct = 0
        row_total = 0
        buckets_match = True
        for key, truth_bucket in truth_buckets.items():
            predicted = pred_buckets.get(key, "default")
            bucket_total += 1
            row_total += 1
            if predicted == truth_bucket:
                bucket_correct += 1
                row_correct += 1
            else:
                buckets_match = False
            if key[1] not in confusion:
                confusion[key[1]] = Counter()
            confusion[key[1]][(truth_bucket, predicted)] += 1

        if token_match:
            matched_correct += row_correct
            matched_total += row_total

        hit = token_match and buckets_match
        intent_hits += hit

        family = row.get("family", "golden")
        stats = per_family[family]
        stats["rows"] += 1
        stats["token_hits"] += int(token_match)
        stats["intent_hits"] += int(hit)
        stats["bucket_correct"] += row_correct
        stats["bucket_total"] += row_total
        stats["matched_correct"] += row_correct if token_match else 0
        stats["matched_total"] += row_total if token_match else 0

    total = max(1, len(rows))
    return {
        "rows": len(rows),
        "token_set_exact": token_hits / total,
        "intent_exact": intent_hits / total,
        "bucket_accuracy": bucket_correct / max(1, bucket_total),
        "bucket_accuracy_on_token_match": matched_correct / max(1, matched_total),
        "per_family": {
            family: {
                "rows": stats["rows"],
                "exact": stats["intent_hits"] / max(1, stats["rows"]),
                "token_set_exact": stats["token_hits"] / max(1, stats["rows"]),
                "bucket_accuracy": stats["bucket_correct"] / max(1, stats["bucket_total"]),
            }
            for family, stats in sorted(per_family.items())
        },
        # Stored as records, not a dict keyed by (truth, predicted) tuples: JSON object
        # keys must be strings, so the tuple form raised TypeError on write.
        "confusion": [
            {"parameter": param, "truth": truth, "predicted": predicted, "count": count}
            for param, counter in confusion.items()
            for (truth, predicted), count in counter.items()
        ],
    }


def held_out_report(
    rows: list[dict],
    intents: list[dict],
    held_out: str,
    control_rows: list[dict],
    control_intents: list[dict],
) -> dict:
    """Detection rate for a token the model never trained on, plus its false-positive rate.

    Detection without a false-positive number is not a result: a model that says yes to
    everything would score 100% here.
    """
    detected = sum(
        1 for row, intent in zip(rows, intents)
        if any(entry["name"] == held_out for entry in intent["tokens"])
    )
    present = sum(
        1 for row in rows if any(entry["name"] == held_out for entry in row["tokens"])
    )
    false_positives = sum(
        1 for intent in control_intents
        if any(entry["name"] == held_out for entry in intent["tokens"])
    )
    return {
        "held_out": held_out,
        "rows": len(rows),
        "rows_with_token": present,
        "detection_rate": detected / max(1, present),
        "false_positive_rate_on_control": false_positives / max(1, len(control_rows)),
        "control_rows": len(control_rows),
    }


def print_families(label: str, baseline: dict, neural: dict) -> None:
    """Per-family intent exact, token-set exact and bucket accuracy, baseline against model.

    A single averaged score cannot say whether the model loses everywhere or collapses on
    two families, and cannot separate a presence failure from a bucket failure. Both matter:
    they have completely different fixes.
    """
    families = sorted(set(baseline.get("per_family", {})) | set(neural.get("per_family", {})))
    if not families:
        return
    print(f"\n{label} -- by family")
    print(
        f"  {'family':<32}{'rows':>6}{'kw exact':>10}{'mdl exact':>11}"
        f"{'mdl tok':>9}{'mdl bkt':>9}"
    )
    for family in families:
        base = baseline.get("per_family", {}).get(family, {})
        neur = neural.get("per_family", {}).get(family, {})
        print(
            f"  {family:<32}{max(base.get('rows', 0), neur.get('rows', 0)):>6}"
            f"{base.get('exact', 0.0):>10.3f}{neur.get('exact', 0.0):>11.3f}"
            f"{neur.get('token_set_exact', 0.0):>9.3f}{neur.get('bucket_accuracy', 0.0):>9.3f}"
        )


def print_report(label: str, result: dict) -> None:
    print(f"\n{label}")
    print(f"  rows                 : {result['rows']}")
    print(f"  token set exact      : {result['token_set_exact']:.3f}")
    print(f"  intent exact         : {result['intent_exact']:.3f}")
    print(f"  bucket accuracy      : {result['bucket_accuracy']:.3f}")
    if "bucket_accuracy_on_token_match" in result:
        print(f"  bucket acc | tok ok  : {result['bucket_accuracy_on_token_match']:.3f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the intent model.")
    parser.add_argument("--checkpoint", default=str(HERE / "model.pt"))
    # Matches what `serve.py` ships (`INTENT_CONFIDENCE`, default "0.50"). Evaluating at a
    # different floor than production reports numbers that describe a configuration nothing
    # runs. The floor was 0.8 until P40 swept it: 0.8 was a convention, and across 0.80 ->
    # 0.30 the live-subset decode is flat above 0.50 while the invented-token count climbs
    # below it (`Saved/sweep_floor.log`).
    parser.add_argument("--confidence", type=float, default=0.50)
    parser.add_argument("--report", default=str(HERE / "eval_report.json"))
    args = parser.parse_args()

    catalogue = load_catalogue()
    held_out = catalogue.get("held_out_token")
    model, vocab = load_checkpoint(args.checkpoint)

    # Training saw every catalogue row except the held-out one. Evaluation adds it back,
    # with no retraining, which is the whole point: the row is the only thing that
    # changed between the two runs.
    trained_on = torch.load(
        args.checkpoint, map_location="cpu", weights_only=False
    ).get("token_names")
    token_names = [t["name"] for t in catalogue["tokens"]]
    print(f"catalogue rows at evaluation : {', '.join(token_names)}")
    if trained_on:
        print(f"rows present during training : {', '.join(trained_on)}")

    splits = {}
    for split in ("test", "golden"):
        path = HERE / f"{split}.jsonl"
        if path.exists():
            splits[split] = read_jsonl(path)

    report: dict = {}
    for name, rows in splits.items():
        keywords = [baseline_intent(row["text"], catalogue) for row in rows]
        neural = model_intents(model, vocab, catalogue, token_names, rows, args.confidence)

        keyword_result = score(rows, keywords, catalogue)
        neural_result = score(rows, neural, catalogue)

        print_report(f"{name}.jsonl -- keyword baseline", keyword_result)
        print_report(f"{name}.jsonl -- intent model", neural_result)
        print_families(f"{name}.jsonl", keyword_result, neural_result)
        heads = bucket_head_report(model, vocab, catalogue, token_names, rows)
        print_bucket_heads(f"{name}.jsonl", heads)
        delta = neural_result["intent_exact"] - keyword_result["intent_exact"]
        verdict = "beats" if delta >= 0 else "LOSES TO"
        print(f"  -> model {verdict} the baseline on intent exact by {delta:+.3f}")

        report[name] = {
            "keyword_baseline": keyword_result,
            "intent_model": neural_result,
            "delta_intent_exact": delta,
            "bucket_heads": heads,
        }

    control_rows = splits.get("test", [])
    control_intents = (
        model_intents(model, vocab, catalogue, token_names, control_rows, args.confidence)
        if control_rows
        else []
    )

    held_path = HERE / "test_heldout.jsonl"
    if held_path.exists():
        held_rows = read_jsonl(held_path)
        held_intents = model_intents(
            model, vocab, catalogue, token_names, held_rows, args.confidence
        )
        report["held_out_token"] = held_out_report(
            held_rows, held_intents, held_out, control_rows, control_intents
        )
        held = report["held_out_token"]
        print(f"\nheld-out token: {held_out}")
        print(f"  rows mentioning it          : {held['rows_with_token']}")
        print(f"  detection rate              : {held['detection_rate']:.3f}")
        print(
            f"  false positive rate on control: "
            f"{held['false_positive_rate_on_control']:.3f} "
            f"({held['control_rows']} rows where it never appears)"
        )

    confusion = report.get("golden", {}).get("intent_model", {}).get("confusion") or []
    if confusion:
        print("\nwhere the buckets go wrong (golden.jsonl):")
        wrong = [
            record
            for record in confusion
            if record["truth"] != record["predicted"] and record["count"]
        ]
        wrong.sort(key=lambda record: (-record["count"], record["parameter"]))
        for record in wrong[:30]:
            print(
                f"  {record['parameter']:<16} truth {record['truth']:<11} -> "
                f"predicted {record['predicted']:<11} x{record['count']}"
            )
        if not wrong:
            print("  no bucket errors on the golden set")

    Path(args.report).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nreport -> {args.report}")


if __name__ == "__main__":
    main()
