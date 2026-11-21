"""FastAPI wrapper: phrase in, buckets out.

The contract, and the reason it is this narrow:

    POST /rules
    {"text": "punchy but slow fireballs",
     "tokens": [{"name": "Fireball", "keywords": ["fireball", "fire", "..."]}, ...]}

    -> {"tokens": [{"name": "Fireball", "params": {"Damage": "higher", "Cooldown": "higher"}}]}

Two properties matter more than anything else here:

* The catalogue arrives **in the request body**, so the service holds no ability table
  of its own. Adding a seventh ability is a game-side data change; nothing is redeployed
  and the token list is not baked into a checkpoint.
* The response contains bucket labels and nothing else. The model never receives a
  min/max range, never multiplies, and cannot emit a gameplay value. `ULoadoutValidator`
  on the C++ side remains the only thing that writes a spec, and it clamps against
  `ParamRanges` exactly as before.

One thing is deliberately NOT the model's job. "without the fireball" names the fireball in order
to remove it, and every forward-reading path -- the model and the keyword parser alike -- equips
it instead, which is why both scored 0.000 on the two exclusion families. That label is set
subtraction, so `exclusion.apply_exclusion` answers it before the model is consulted and only
when the phrase carries an exclusion marker. It emits token names with every bucket at `default`,
so it cannot invent a parameter change either.

The answering layer is reported in the X-Intent-Source response header (exclusion / clauses /
model / no-catalogue) rather than in the body. The body's shape is load-bearing: both
WishCraftPlayerController and ULoadoutValidator require the root object to hold exactly one field,
so an extra JSON key would be rejected as an invalid response.

If nothing clears the confidence floor the response is an empty list, which is the
signal to fall back to the keyword parser: an empty list is a much better failure than
a confident wrong loadout.

A second thing is not the model's job either. "shorter cooldowns and heavier fireballs" moves
TWO parameters at once, and P21/P22/P25 all failed to teach that in the weights: the model binds
one adjective and loses the other. `clauses.apply_clauses` answers it before the model and only
when the evidence is complete -- two distinct parameters, or one when the phrase has no conjunction
at all -- and refuses everything else, so the model keeps every phrase the rule has no exact
reading for. 198 live rows fired, 188 right.

Run:
    uvicorn serve:app --host 127.0.0.1 --port 8077
    curl -s localhost:8077/health
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Response
from pydantic import BaseModel

from clauses import apply_clauses
from exclusion import apply_exclusion
from schema import category_caps, load_catalogue, tokens_by_name
from train import load_checkpoint, predict

HERE = Path(__file__).parent
CHECKPOINT = os.environ.get("INTENT_CHECKPOINT", str(HERE / "model.pt"))
CONFIDENCE = float(os.environ.get("INTENT_CONFIDENCE", "0.50"))

app = FastAPI(title="WishCraft intent model", version="1.0")

_model = None
_vocab = None


def get_model():
    """Load once, on first request. Training and serving must agree on the checkpoint."""
    global _model, _vocab
    if _model is None:
        _model, _vocab = load_checkpoint(CHECKPOINT)
    return _model, _vocab


class RuleRequest(BaseModel):
    text: str
    tokens: list[dict]


def build_catalogue(request_tokens: list[dict]) -> dict:
    """Merge the request's token rows over the local catalogue.

    The request is authoritative for name and keywords -- that is what makes the service
    vocabulary-agnostic. Ranges are only needed to know how many parameters a token has,
    and the model treats each parameter as text, so a row with no ranges simply produces
    an ability with no tunable parameters rather than an error.
    """
    local = tokens_by_name(load_catalogue())
    merged = []

    for entry in request_tokens:
        name = entry.get("name")
        if not name:
            continue
        base = dict(local.get(name, {}))
        base.update({key: value for key, value in entry.items() if value is not None})
        base["name"] = name
        base.setdefault("display_name", name)
        base.setdefault("keywords", [])
        base.setdefault("ranges", {})
        base.setdefault("category", "Offense")
        merged.append(base)

    return {
        "tokens": merged,
        "category_caps": category_caps(load_catalogue()),
    }


@app.get("/health")
def health() -> dict:
    model, vocab = get_model()
    return {
        "status": "ok",
        "checkpoint": CHECKPOINT,
        "vocab_words": len(vocab.words),
        "confidence_floor": CONFIDENCE,
    }


@app.post("/rules")
def rules(request: RuleRequest, response: Response) -> dict:
    model, vocab = get_model()
    catalogue = build_catalogue(request.tokens)
    token_names = [token["name"] for token in catalogue["tokens"]]

    if not token_names:
        response.headers["X-Intent-Source"] = "no-catalogue"
        return {"tokens": []}

    # Set subtraction before the model: a complement request is not a classification problem.
    # Only fires on an explicit exclusion marker, so no other phrasing reaches it.
    exclusion = apply_exclusion(request.text, catalogue, token_names)
    if exclusion is not None:
        response.headers["X-Intent-Source"] = "exclusion"
        return {
            "tokens": [
                {"name": entry["name"], "params": entry["params"]}
                for entry in exclusion["tokens"]
            ]
        }

    # Two parameters in one sentence: an exact reading, before the model is consulted. Refuses
    # every phrase it cannot resolve completely, so no single-parameter phrasing reaches it.
    clauses = apply_clauses(request.text, catalogue, token_names)
    if clauses is not None:
        response.headers["X-Intent-Source"] = "clauses"
        return {
            "tokens": [
                {"name": entry["name"], "params": entry["params"]}
                for entry in clauses["tokens"]
            ]
        }

    result = predict(
        model,
        vocab,
        request.text,
        catalogue,
        token_names,
        confidence=CONFIDENCE,
    )

    # Buckets only. Nothing else crosses this boundary.
    response.headers["X-Intent-Source"] = "model"
    return {
        "tokens": [
            {"name": entry["name"], "params": entry["params"]}
            for entry in result["tokens"]
        ]
    }
