"""Score the model on the candidate set the game actually ships.

Why this exists: `evaluate.py` scores `test.jsonl` against every row in `catalogue.json`
-- six abilities, one of which (`Blink`) is the deliberate held-out token the model never
trained on, and two of which (`LightningStrike`, `Shield`) cannot be equipped because
`/Game/Sandbox/DA_AbilityTokens` does not contain them. `catalogue_live.json` is the mirror
of that asset, so this script answers a different and more honest question: given the three
abilities a player can actually choose from, how often does the model pick the right set?

Rows whose label names an ability outside the live set are dropped rather than scored as
misses. They are unanswerable by construction -- no candidate corresponds to the answer --
and counting them would understate the model for a reason that does not exist in play.

Two numbers are reported. `intent model` is the checkpoint alone, which is the model benchmark.
`shipped` is the checkpoint behind the two deterministic rules, which is what serve.py runs: an
exclusion phrase is answered by set subtraction, a two-clause phrase by `clauses.apply_clauses`,
and only what is left reaches the model. They are kept apart on purpose -- only one of them is a
fact about the weights.

Run:
    python eval_live.py                  # scores model.pt
    python eval_live.py model_p19_backup.pt   # scores a specific checkpoint
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from evaluate import baseline_intent, model_intents, print_families, print_report, score
from clauses import apply_clauses
from exclusion import apply_exclusion
from train import load_checkpoint, read_jsonl

HERE = Path(__file__).parent
CONFIDENCE = 0.50  # what serve.py ships (INTENT_CONFIDENCE); swept in Saved/sweep_floor.log


def main() -> None:
    live = json.loads((HERE / "catalogue_live.json").read_text())
    names = [token["name"] for token in live["tokens"]]

    rows = read_jsonl(HERE / "test.jsonl")
    keep = [row for row in rows if all(entry["name"] in names for entry in row["tokens"])]

    print("live candidates :", ", ".join(names))
    print("test rows       :", f"{len(keep)} of {len(rows)} kept")
    dropped = len(rows) - len(keep)
    print("dropped         :", f"{dropped} label an ability the game cannot equip")

    model, vocab = load_checkpoint(str(HERE / (sys.argv[1] if len(sys.argv) > 1 else "model.pt")))

    baseline = score(keep, [baseline_intent(row["text"], live) for row in keep], live)
    neural_intents = model_intents(model, vocab, live, names, keep, CONFIDENCE)
    neural = score(keep, neural_intents, live)

    # The shipped path: both deterministic rules first, everything else by the model. Resolved
    # against the full catalogue, because a sentence may name an ability the game cannot equip
    # and that word must still not be read as naming a live one.
    full = json.loads((HERE / "catalogue.json").read_text())
    rules = [apply_exclusion(row["text"], full, names) for row in keep]
    clauses = [apply_clauses(row["text"], full, names) for row in keep]
    shipped = score(
        keep,
        [
            exclusion if exclusion is not None
            else clause if clause is not None
            else intent
            for exclusion, clause, intent in zip(rules, clauses, neural_intents)
        ],
        live,
    )

    print_report("live 3-token -- keyword baseline", baseline)
    print_report("live 3-token -- intent model", neural)
    print_report("live 3-token -- shipped (exclusion + clauses + model)", shipped)
    print("exclusion rule fired on", sum(1 for rule in rules if rule is not None), "rows")
    print("clause rule fired on", sum(1 for rule in clauses if rule is not None), "rows")
    print_families("live 3-token", baseline, neural)


if __name__ == "__main__":
    main()
