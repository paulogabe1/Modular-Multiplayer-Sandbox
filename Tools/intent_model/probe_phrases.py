"""Read the exact phrases that failed, from any checkpoint, on the live 3-token set.

The aggregate metrics cannot tell you whether the `faster` collision is fixed -- a change
that helps one phrase and breaks another nets out to roughly zero. This prints the phrases.

Lives in Tools/intent_model/ so that `import evaluate` / `import train` resolve (this
directory is sys.path[0] when the script is run by path).

Run:
    python probe_phrases.py model_p19_backup.pt model.pt
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from train import load_checkpoint, predict

HERE = Path(__file__).parent

# Phrase, and the answer that would be right. `None` means "equip nothing".
PROBES = [
    ("stronger and faster fireballs", {"Fireball": {"ProjectileSpeed": "higher"}}),
    ("faster fireballs", {"Fireball": {"ProjectileSpeed": "higher"}}),
    ("slower fireballs", {"Fireball": {"ProjectileSpeed": "lower"}}),
    ("make the fireball faster", {"Fireball": {"ProjectileSpeed": "higher"}}),
    (
        "make the fireball strong and fast",
        {"Fireball": {"Damage": "higher", "ProjectileSpeed": "higher"}},
    ),
    ("make the speed boost faster", {"SpeedBoost": {"Multiplier": "higher"}}),
    ("faster speed boosts", {"SpeedBoost": {"Multiplier": "higher"}}),
    ("give me quick speed boosts", {"SpeedBoost": {"Multiplier": "higher"}}),
    (
        "make the fireball punchy but slow",
        {"Fireball": {"Damage": "higher", "Cooldown": "higher"}},
    ),
    (
        "give the speed boost more duration and more multiplier",
        {"SpeedBoost": {"Duration": "higher", "Multiplier": "higher"}},
    ),
    ("make the fireball do maximum damage", {"Fireball": {"Damage": "max"}}),
    (
        "give the speed boost quicker cooldowns and a bigger multiplier",
        {"SpeedBoost": {"Cooldown": "lower", "Multiplier": "higher"}},
    ),
    ("make me a sandwich", None),
    # --- Equip-only. An ability named by itself changes nothing. Measured on P20: `fireball`
    # returned ProjectileSpeed LOWER and `stealth` returned Cooldown LOWER, while
    # `invisibility`, `speed boost` and `cloak` were correct -- an unstable split, which is why
    # these are pinned here rather than left to the aggregate.
    ("fireball", {"Fireball": {}}),
    ("invisibility", {"Invisibility": {}}),
    ("speed boost", {"SpeedBoost": {}}),
    ("stealth", {"Invisibility": {}}),
    ("cloak", {"Invisibility": {}}),
    ("give me the fireball", {"Fireball": {}}),
]


def as_expected(output: dict) -> dict:
    """The `predict` result reduced to {token: {param: bucket}} with defaults stripped."""
    return {
        entry["name"]: {
            param: bucket for param, bucket in entry["params"].items() if bucket != "default"
        }
        for entry in output["tokens"]
    }


def main() -> None:
    live = json.loads((HERE / "catalogue_live.json").read_text(encoding="utf-8"))
    names = [token["name"] for token in live["tokens"]]

    checkpoints = sys.argv[1:] or ["model.pt"]
    loaded = [(Path(p).name, *load_checkpoint(str(HERE / Path(p).name))) for p in checkpoints]

    for phrase, expected in PROBES:
        print(f"\n{phrase!r}")
        print(f"    want  {expected if expected is not None else 'nothing'}")
        for label, model, vocab in loaded:
            got = as_expected(predict(model, vocab, phrase, live, names, confidence=0.8))
            ok = got == (expected or {})
            print(f"    {label:<26} {'OK  ' if ok else 'MISS'} {got if got else 'nothing'}")


if __name__ == "__main__":
    main()
