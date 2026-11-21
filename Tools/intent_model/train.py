"""Train the two-tower intent model.

Architecture, and why it is shaped this way:

  phrase encoder  ->  one vector per sentence
  text encoder    ->  one vector per catalogue row's *text*, and per parameter name
  presence        =   sigmoid(phrase . token)          (a dot product, so N rows is an
                                                        N-row matrix, not N heads)
  buckets         =   one shared MLP over [phrase, token, parameter]

Three consequences fall out of that shape, and they are the reason for it:

1. Adding a seventh ability adds a catalogue row. There is no head to widen, no
   architecture change, no retraining required by construction.
2. One bucket head conditioned on the *parameter's text* serves `Duration` on three
   different abilities and gives a sensible vector to a parameter name it never
   trained on, because the parameter goes through the same text encoder as everything
   else.
3. Because both towers read text, a new ability whose name and keywords already look
   like language the encoder has seen is detectable with no gradient steps at all.
   Character trigrams are what give an unseen *word* a non-degenerate vector: without
   them every out-of-vocabulary name collapses to the same `<unk>` embedding and the
   zero-shot claim is dead on arrival.

Every word is emitted as `[word_id, tri1, tri2, ...]`, so the sequence carries both
lexical and sub-lexical evidence through the same transformer.

Usage:
    python train.py                       # 30 epochs, best checkpoint to model.pt
    python train.py --epochs 60 --lr 2e-4
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

from generate_dataset import pretty_param
from schema import (
    BUCKETS,
    BUCKET_TO_INDEX,
    DEFAULT_BUCKET,
    DEFAULT_INDEX,
    INDEX_TO_BUCKET,
    MOVED_BUCKETS,
    MOVED_INDICES,
    MOVED_TO_INDEX,
    load_catalogue,
    param_ranges,
    tokens_by_name,
)

HERE = Path(__file__).parent
WORD_RE = re.compile(r"[a-z0-9']+")
NGRAM_BUCKETS = 2048


# ---------------------------------------------------------------- tokenisation


def normalise(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def trigram_hashes(word: str) -> list[int]:
    """Stable character trigram hashes for one word.

    Boundary markers make `^bl` and `nk$` distinct from their mid-word forms, which is
    what lets `blink` resemble `blinking` rather than resemble every word containing
    `lin`.
    """
    wrapped = f"^{word}$"
    grams = {wrapped[i : i + 3] for i in range(len(wrapped) - 2)} if len(wrapped) >= 3 else {wrapped}
    hashes = []
    for gram in sorted(grams):
        value = 2166136261
        for character in gram:
            value = ((value ^ ord(character)) * 16777619) & 0xFFFFFFFF
        hashes.append(value % NGRAM_BUCKETS)
    return hashes


class Vocab:
    """Word ids plus a fixed bank of trigram ids, so OOV words still carry signal."""

    def __init__(self, words: set[str], max_len: int):
        self.max_len = max_len
        self.words = ["<pad>", "<unk>"] + sorted(words)
        self.index = {word: i for i, word in enumerate(self.words)}
        self.ngram_base = len(self.words)
        self.size = self.ngram_base + NGRAM_BUCKETS

    def encode(self, text: str) -> list[int]:
        """Always returns exactly `max_len` ids, zero-padded.

        Fixed length matters: every caller builds a tensor from a list of these, and a
        ragged list cannot become a rectangular tensor.
        """
        ids: list[int] = []
        for word in normalise(text):
            ids.append(self.index.get(word, 1))
            for hashed in trigram_hashes(word):
                ids.append(self.ngram_base + hashed)
            if len(ids) >= self.max_len:
                break
        ids = ids[: self.max_len] or [1]
        return ids + [0] * (self.max_len - len(ids))


def token_text(token: dict) -> str:
    """The text a catalogue row is represented by. Name, display name and keywords.

    Everything about this string comes from the catalogue, so a token nobody wrote code
    for is represented exactly as well as one that was.
    """
    parts = [token.get("display_name") or token["name"], token["name"]]
    parts.extend(token.get("keywords") or [])
    return " ".join(parts)


# ---------------------------------------------------------------- model


class Encoder(nn.Module):
    """Small Transformer over the id sequence, mean-pooled and L2-normalised."""

    def __init__(
        self,
        vocab_size: int,
        max_len: int,
        d_model: int,
        nhead: int,
        layers: int,
        dim_feedforward: int,
        dropout: float,
    ):
        super().__init__()
        self.embed = nn.Embedding(vocab_size, d_model, padding_idx=0)
        self.position = nn.Embedding(max_len, d_model)
        layer = nn.TransformerEncoderLayer(
            d_model,
            nhead,
            dim_feedforward,
            dropout,
            batch_first=True,
            norm_first=True,
            activation="gelu",
        )
        self.encoder = nn.TransformerEncoder(layer, layers, enable_nested_tensor=False)
        self.norm = nn.LayerNorm(d_model)

    def forward(
        self,
        ids: torch.Tensor,
        return_peak: bool = False,
        return_states: bool = False,
    ):
        pad_mask = ids.eq(0)
        length = ids.size(1)
        positions = torch.arange(length, device=ids.device).unsqueeze(0)
        hidden = self.embed(ids) + self.position(positions)
        hidden = self.encoder(hidden, src_key_padding_mask=pad_mask)
        keep = (~pad_mask).float().unsqueeze(-1)
        pooled = (hidden * keep).sum(1) / keep.sum(1).clamp(min=1.0)
        pooled = F.normalize(self.norm(pooled), dim=-1)
        if return_states:
            # The per-token states, not pooled. Anything that needs to know *which* word
            # said something must come through here; pooling has already thrown that away.
            return pooled, hidden, pad_mask
        if not return_peak:
            return pooled
        # Max pooling keeps the single strongest word in the phrase, which the mean
        # averages away: "max cooldown" and "max duration" have near-identical means.
        # It still does not know which parameter is being asked about, so it cannot
        # resolve polarity; `IntentModel.attend` is what replaced it in the bucket head.
        masked = hidden.masked_fill(pad_mask.unsqueeze(-1), -1e9)
        peak = F.normalize(self.norm(masked.max(dim=1).values), dim=-1)
        return pooled, peak


class IntentModel(nn.Module):
    def __init__(
        self,
        vocab_size: int,
        max_len: int,
        d_model: int = 128,
        nhead: int = 4,
        layers: int = 2,
        dim_feedforward: int = 256,
        dropout: float = 0.1,
        use_pooled_phrase: bool = True,
    ):
        super().__init__()
        self.phrase_encoder = Encoder(
            vocab_size, max_len, d_model, nhead, layers, dim_feedforward, dropout
        )
        # One encoder instance, used for BOTH token text and parameter names. Sharing it
        # is what keeps the two towers in the same space.
        self.text_encoder = Encoder(
            vocab_size, max_len, d_model, nhead, layers, dim_feedforward, dropout
        )
        self.temperature = nn.Parameter(torch.tensor(0.1))
        # Presence is scored with the candidate SET in view, so the head can rank rather
        # than merely match. Inputs: phrase, candidate, candidate-set mean, this
        # candidate's similarity, and its similarity relative to the best candidate.
        self.presence_head = nn.Sequential(
            nn.Linear(3 * d_model + 2, 256),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(256, 1),
        )
        # The bucket decision is factorised into two questions trained on DISJOINT row
        # sets, because they are not the same question:
        #
        #   moved_head      "does this phrase move this parameter at all?"
        #   direction_head  "if it moves, which of much_lower/lower/higher/max?"
        #
        # A single 5-way softmax makes the second question compete with a `default` class
        # that is ~85% of all labels, so the cheapest way for a small model to keep the
        # rare buckets alive is to gamble on them. That is what the old pow(0.2) class
        # weights were compensating for, and the gamble showed up as parameters moving
        # that the phrase never mentioned. Masking the direction head's loss to rows that
        # actually moved removes the competition instead of re-weighting it, so the
        # direction head never sees an unchanged row and needs no `default` weight at all.
        # The two heads share a trunk, so the phrase/param binding is learned once.
        # Polarity is a per-parameter decision: "slower" is `higher` for Cooldown and
        # `lower` for ProjectileSpeed. A pooled phrase concatenated with a parameter name
        # cannot express that product -- mean pooling has discarded which word said
        # "slower", so the head can only memorise each pairing. Here the parameter name
        # queries the phrase token by token, and the head receives what it selected.
        self.param_attention = nn.MultiheadAttention(
            d_model, nhead, dropout=dropout, batch_first=True
        )
        self.attention_norm = nn.LayerNorm(d_model)
        # `use_pooled_phrase=False` withholds the parameter-agnostic phrase from the trunk.
        # It is an ablation of the shortcut described in `bucket_heads`, not a new head: the
        # only column removed is the one `attend()` was supposed to render unnecessary.
        self.use_pooled_phrase = use_pooled_phrase
        trunk_width = (4 if use_pooled_phrase else 3) * d_model
        self.bucket_trunk = nn.Sequential(
            nn.Linear(trunk_width, 256),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(256, 128),
            nn.GELU(),
        )
        self.moved_head = nn.Linear(128, 1)
        self.direction_head = nn.Linear(128, len(MOVED_BUCKETS))

    def presence_logits(self, phrase: torch.Tensor, tokens: torch.Tensor) -> torch.Tensor:
        """Score every candidate against the phrase WITH the candidate set in view.

        A bare dot product scores each candidate independently, which cannot express
        "everything except X": that answer is a property of the whole set, not of one
        pair. Feeding the set mean plus this candidate's similarity relative to the best
        match lets the head reason about rank and exclusion.
        """
        rows, width = phrase.shape
        candidates = tokens.size(0)
        similarity = (phrase @ tokens.T) / self.temperature.clamp(min=1e-3)
        best = similarity.max(dim=1, keepdim=True).values
        scalars = torch.stack([similarity, similarity - best], dim=-1)

        features = torch.cat(
            [
                phrase.unsqueeze(1).expand(rows, candidates, width),
                tokens.unsqueeze(0).expand(rows, candidates, width),
                tokens.mean(0).view(1, 1, width).expand(rows, candidates, width),
                scalars,
            ],
            dim=-1,
        )
        return self.presence_head(features).squeeze(-1)

    def attend(
        self,
        states: torch.Tensor,
        key_padding_mask: torch.Tensor,
        param: torch.Tensor,
    ) -> torch.Tensor:
        """What the phrase says about *this* parameter, selected token by token.

        `states` is the phrase encoder's per-token hidden state, `(rows, length, d)`, and
        `param` is the parameter name's pooled embedding, `(rows, d)`. The parameter is
        the only query, so the result is a phrase summary conditioned on which parameter
        is being asked about -- the one thing mean and max pooling both discard.

        `key_padding_mask` keeps padding out of the attention weights. Without it the
        pads would collect weight from every query and the context would drift toward a
        constant, which is the failure mode that looks like "attention did nothing".
        """
        context, _ = self.param_attention(
            param.unsqueeze(1),
            states,
            states,
            key_padding_mask=key_padding_mask,
            need_weights=False,
        )
        return F.normalize(self.attention_norm(context.squeeze(1)), dim=-1)

    def bucket_heads(
        self,
        phrase: torch.Tensor,
        context: torch.Tensor,
        token: torch.Tensor,
        param: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Raw `(moved_logit, direction_logits)` per `[phrase, context, token, param]` row.

        `context` is the parameter-conditioned attention output from `attend()`. It sits
        where the max-pooled `peak` used to be, at the same width, so the trunk and every
        downstream decode stay unchanged in shape and index semantics.

        Returned as two tensors rather than one 5-vector so the loss can train them on
        different row sets; `bucket_probs` recombines them for decoding.

        When `use_pooled_phrase` is False the parameter-agnostic `phrase` is withheld. It
        carries the whole sentence's meaning, so with it present the head can answer "the
        phrase says *slower*" without ever consulting which parameter is being asked about --
        the shortcut that made `punchy but slow` move ProjectileSpeed `lower` instead of
        Cooldown `higher`. Withheld, the only route to the label is `context`, the
        parameter-conditioned attention from `attend()`, which is where binding lives.
        `phrase` is still used by `presence_logits`, so token presence is untouched by this.
        """
        if self.use_pooled_phrase:
            features = torch.cat([phrase, context, token, param], dim=-1)
        else:
            features = torch.cat([context, token, param], dim=-1)
        hidden = self.bucket_trunk(features)
        return self.moved_head(hidden).squeeze(-1), self.direction_head(hidden)

    def bucket_probs(
        self,
        phrase: torch.Tensor,
        context: torch.Tensor,
        token: torch.Tensor,
        param: torch.Tensor,
    ) -> torch.Tensor:
        """An `(n, 5)` distribution in `BUCKETS` order, assembled from the two heads.

        `p(default) = 1 - P(moved)` and `p(bucket) = P(moved) * P(bucket | moved)`, which
        normalises by construction. Decoding stays a plain argmax over `BUCKETS` order, so
        every existing call site keeps its index semantics.
        """
        moved_logit, direction_logits = self.bucket_heads(phrase, context, token, param)
        moved = torch.sigmoid(moved_logit)
        probs = torch.zeros(moved.size(0), len(BUCKETS), device=moved.device)
        probs[:, DEFAULT_INDEX] = 1.0 - moved
        probs[:, MOVED_INDICES] = moved.unsqueeze(1) * F.softmax(direction_logits, dim=-1)
        return probs


# ---------------------------------------------------------------- data


class Tensors:
    """Everything the model needs about the catalogue, computed once."""

    def __init__(self, catalogue: dict, token_names: list[str], vocab: Vocab):
        self.token_names = token_names
        self.index = {name: i for i, name in enumerate(token_names)}
        known = tokens_by_name(catalogue)
        self.params = [list(param_ranges(known[name])) for name in token_names]
        self.param_slots = max(len(p) for p in self.params)

        self.token_ids = torch.tensor(
            [vocab.encode(token_text(known[name])) for name in token_names], dtype=torch.long
        )
        self.param_ids = torch.zeros(
            len(token_names), self.param_slots, vocab.max_len, dtype=torch.long
        )
        for token_i, names in enumerate(self.params):
            for slot, param_name in enumerate(names):
                self.param_ids[token_i, slot] = torch.tensor(
                    vocab.encode(pretty_param(param_name)), dtype=torch.long
                )

    def batch(self, rows: list[dict], vocab: Vocab) -> dict:
        count = len(rows)
        phrase_ids = torch.zeros(count, vocab.max_len, dtype=torch.long)
        presence = torch.zeros(count, len(self.token_names))
        buckets = torch.full((count, len(self.token_names), self.param_slots), -100, dtype=torch.long)

        for row_i, row in enumerate(rows):
            phrase_ids[row_i] = torch.tensor(vocab.encode(row["text"]), dtype=torch.long)
            for entry in row["tokens"]:
                token_i = self.index.get(entry["name"])
                if token_i is None:
                    continue
                presence[row_i, token_i] = 1.0
                for slot, param_name in enumerate(self.params[token_i]):
                    bucket = entry["params"].get(param_name, DEFAULT_BUCKET)
                    buckets[row_i, token_i, slot] = BUCKET_TO_INDEX[bucket]

        return {
            "phrase_ids": phrase_ids,
            "presence": presence,
            "buckets": buckets,
        }


def read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build_vocab(rows: list[dict], catalogue: dict, max_len: int) -> Vocab:
    words: set[str] = set()
    for row in rows:
        words.update(normalise(row["text"]))
    for token in catalogue["tokens"]:
        words.update(normalise(token_text(token)))
        for param_name in token["ranges"]:
            words.update(normalise(pretty_param(param_name)))
    return Vocab(words, max_len)


# ---------------------------------------------------------------- loss & metrics


# Maps a full bucket index onto its direction-head index, with `default` mapped to -1
# because an unchanged row can never be a direction target.
BUCKET_TO_DIRECTION = [MOVED_TO_INDEX.get(INDEX_TO_BUCKET[i], -1) for i in range(len(BUCKETS))]


# No class weights for the direction head. Masking the row set already removed the
# imbalance that mattered -- `default` is ~85% of labels and the direction head never sees
# an unchanged row -- so the remaining spread between the four moved buckets is small. A
# sqrt-inverse-frequency weighting was tried and dropped, but the metric that was supposed
# to justify it (`bucket_direction_accuracy` on the valid split) turned out to be a split
# artefact: valid is the reduce-only families, so it measured disagreement with a
# distribution the model never trained on. Direction is judged by
# `evaluate.bucket_head_report` on a representative split instead.


def compute_loss(
    model: IntentModel,
    tensors: Tensors,
    batch: dict,
    pos_weight: float = 2.0,
) -> tuple[torch.Tensor, dict]:
    phrase, states, pad_mask = model.phrase_encoder(batch["phrase_ids"], return_states=True)
    token_vectors = model.text_encoder(tensors.token_ids)
    param_vectors = model.text_encoder(
        tensors.param_ids.view(-1, tensors.param_ids.size(-1))
    ).view(tensors.param_ids.size(0), tensors.param_slots, -1)

    logits = model.presence_logits(phrase, token_vectors)
    presence_loss = F.binary_cross_entropy_with_logits(
        logits, batch["presence"], pos_weight=torch.tensor(pos_weight)
    )

    buckets = batch["buckets"]
    mask = buckets.ne(-100)
    moved_loss = torch.zeros(())
    direction_loss = torch.zeros(())
    if mask.any():
        row_i, token_i, slot_i = mask.nonzero(as_tuple=True)
        target = buckets[row_i, token_i, slot_i]
        context = model.attend(states[row_i], pad_mask[row_i], param_vectors[token_i, slot_i])
        moved_logit, direction_logits = model.bucket_heads(
            phrase[row_i], context, token_vectors[token_i], param_vectors[token_i, slot_i]
        )

        # Question 1, asked of every row: unchanged, or moved? No class weights: the
        # natural base rate is the correct answer for an unmentioned parameter, and it is
        # exactly what the old weighting taught the model to ignore.
        moved_target = target.ne(DEFAULT_INDEX).float()
        moved_loss = F.binary_cross_entropy_with_logits(moved_logit, moved_target)

        # Question 2, asked only of rows that actually moved. Masking the row set, rather
        # than re-weighting a shared five-way softmax, is the change that stops the head
        # from moving parameters no phrase mentioned.
        moved_rows = target.ne(DEFAULT_INDEX)
        if moved_rows.any():
            direction_target = torch.tensor(
                [BUCKET_TO_DIRECTION[t] for t in target[moved_rows].tolist()],
                dtype=torch.long,
                device=target.device,
            )
            direction_loss = F.cross_entropy(
                direction_logits[moved_rows], direction_target
            )
        bucket_loss = moved_loss + direction_loss
    else:
        bucket_loss = torch.zeros(())

    total = presence_loss + bucket_loss
    return total, {
        "presence": presence_loss.item(),
        "bucket": bucket_loss.item(),
        "moved": moved_loss.item(),
        "direction": direction_loss.item(),
    }


@torch.no_grad()
def evaluate(model: IntentModel, tensors: Tensors, batch: dict) -> dict:
    """Presence F1, bucket accuracy and exact intent match for one batch."""
    model.eval()
    phrase, states, pad_mask = model.phrase_encoder(batch["phrase_ids"], return_states=True)
    token_vectors = model.text_encoder(tensors.token_ids)
    param_vectors = model.text_encoder(
        tensors.param_ids.view(-1, tensors.param_ids.size(-1))
    ).view(tensors.param_ids.size(0), tensors.param_slots, -1)

    presence_prob = torch.sigmoid(model.presence_logits(phrase, token_vectors))
    predicted = presence_prob > 0.5
    truth = batch["presence"] > 0.5

    true_positive = (predicted & truth).sum().item()
    false_positive = (predicted & ~truth).sum().item()
    false_negative = (~predicted & truth).sum().item()
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    f1 = 2 * precision * recall / max(1e-9, precision + recall)

    buckets = batch["buckets"]
    mask = buckets.ne(-100)
    row_i, token_i, slot_i = mask.nonzero(as_tuple=True)
    moved_accuracy = 0.0
    direction_accuracy = 0.0
    if row_i.numel():
        target = buckets[row_i, token_i, slot_i]
        context = model.attend(states[row_i], pad_mask[row_i], param_vectors[token_i, slot_i])
        moved_logit, direction_logits = model.bucket_heads(
            phrase[row_i], context, token_vectors[token_i], param_vectors[token_i, slot_i]
        )
        bucket_prediction = model.bucket_probs(
            phrase[row_i], context, token_vectors[token_i], param_vectors[token_i, slot_i]
        ).argmax(-1)
        bucket_accuracy = (
            (bucket_prediction == buckets[row_i, token_i, slot_i]).float().mean().item()
        )
        bucket_correct = bucket_prediction == buckets[row_i, token_i, slot_i]

        # Report the two questions separately. A five-way accuracy alone cannot say which
        # half of a factorised head regressed on a given run.
        moved_accuracy = (
            moved_logit.gt(0).long() == target.ne(DEFAULT_INDEX).long()
        ).float().mean().item()
        moved_rows = target.ne(DEFAULT_INDEX)
        if moved_rows.any():
            direction_prediction = direction_logits[moved_rows].argmax(-1)
            direction_target = torch.tensor(
                [BUCKET_TO_DIRECTION[t] for t in target[moved_rows].tolist()],
                dtype=torch.long,
                device=direction_prediction.device,
            )
            direction_accuracy = (
                (direction_prediction == direction_target).float().mean().item()
            )
    else:
        bucket_accuracy = 0.0
        bucket_correct = torch.zeros(0, dtype=torch.bool)

    presence_correct = (predicted == truth).all(dim=1)
    if row_i.numel():
        bucket_ok = torch.zeros(batch["presence"].size(0), dtype=torch.bool)
        bucket_ok[row_i] = bucket_correct
        exact = (presence_correct & bucket_ok).float().mean().item()
    else:
        exact = presence_correct.float().mean().item()

    return {
        "presence_f1": f1,
        "presence_precision": precision,
        "presence_recall": recall,
        "bucket_accuracy": bucket_accuracy,
        "bucket_moved_accuracy": moved_accuracy,
        "bucket_direction_accuracy": direction_accuracy,
        "exact_match": exact,
    }


# ---------------------------------------------------------------- inference


def load_checkpoint(path: str | Path, device: str = "cpu") -> tuple[IntentModel, Vocab]:
    payload = torch.load(Path(path), map_location=device, weights_only=False)
    vocab = Vocab(set(), payload["config"]["max_len"])
    vocab.words = payload["vocab"]
    vocab.index = {word: i for i, word in enumerate(vocab.words)}
    vocab.ngram_base = len(vocab.words)
    vocab.size = vocab.ngram_base + NGRAM_BUCKETS

    model = IntentModel(vocab.size, **payload["config"])
    model.load_state_dict(payload["state_dict"])
    model.eval()
    return model, vocab


@torch.no_grad()
def predict(
    model: IntentModel,
    vocab: Vocab,
    text: str,
    catalogue: dict,
    token_names: list[str],
    confidence: float = 0.8,
) -> dict:
    """Phrase in, buckets out. This is the exact function `serve.py` exposes.

    Deliberately returns bucket labels and nothing else: the model never sees a range,
    never divides, and cannot emit a value. `schema.bucket_to_value` on the game side
    turns `higher` into 40 damage and clamps it.
    """
    known = tokens_by_name(catalogue)
    tensors = Tensors(catalogue, token_names, vocab)

    phrase_ids = torch.tensor([vocab.encode(text)], dtype=torch.long)
    phrase, states, pad_mask = model.phrase_encoder(phrase_ids, return_states=True)
    token_vectors = model.text_encoder(tensors.token_ids)
    probabilities = torch.sigmoid(model.presence_logits(phrase, token_vectors))[0]

    selected = [i for i, p in enumerate(probabilities.tolist()) if p >= confidence]
    selected.sort(key=lambda i: -probabilities[i].item())

    output = []
    for token_i in selected:
        name = token_names[token_i]
        slot_count = len(tensors.params[token_i])
        if slot_count == 0:
            output.append({"name": name, "params": {}})
            continue
        param_vectors = model.text_encoder(
            tensors.param_ids[token_i, :slot_count]
        )
        # The context depends on (phrase, parameter) only, so it is computed once per
        # slot instead of being expanded from a single pooled vector.
        context = model.attend(
            states.expand(slot_count, -1, -1),
            pad_mask.expand(slot_count, -1),
            param_vectors,
        )
        probs = model.bucket_probs(
            phrase.expand(slot_count, -1),
            context,
            token_vectors[token_i].expand(slot_count, -1),
            param_vectors,
        )
        labels = probs.argmax(-1).tolist()
        params = {
            param_name: BUCKETS[label]
            for param_name, label in zip(tensors.params[token_i], labels)
        }
        output.append({"name": name, "params": params})

    return {"tokens": output, "scores": {token_names[i]: round(probabilities[i].item(), 3) for i in range(len(token_names))}}


# ---------------------------------------------------------------- training


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the intent model.")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--lr", type=float, default=3e-4)
    parser.add_argument("--max-len", type=int, default=48)
    parser.add_argument("--d-model", type=int, default=128)
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--heads", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20261108)
    parser.add_argument("--patience", type=int, default=100)
    parser.add_argument(
        "--no-pooled-phrase",
        action="store_true",
        help="Withhold the parameter-agnostic pooled phrase from the bucket trunk.",
    )
    parser.add_argument("--out", default=str(HERE / "model.pt"))
    args = parser.parse_args()

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"device        : {device}")
    if device == "cuda":
        print(f"gpu           : {torch.cuda.get_device_name(0)}")

    catalogue = load_catalogue()
    held_out = catalogue.get("held_out_token")
    train_rows = read_jsonl(HERE / "train.jsonl")
    valid_rows = read_jsonl(HERE / "valid.jsonl")

    # The candidates the model trains against are the catalogue minus the held-out token.
    # Nothing tells the model that token exists; `evaluate.py` adds it back.
    token_names = [t["name"] for t in catalogue["tokens"] if t["name"] != held_out]
    print(f"held-out token: {held_out}")
    print(f"candidates    : {', '.join(token_names)}")

    vocab = build_vocab(train_rows + valid_rows, catalogue, args.max_len)
    print(f"vocab         : {len(vocab.words)} words + {NGRAM_BUCKETS} trigram buckets")
    print(f"train / valid : {len(train_rows)} / {len(valid_rows)}")

    tensors = Tensors(catalogue, token_names, vocab)

    model = IntentModel(
        vocab.size,
        args.max_len,
        d_model=args.d_model,
        nhead=args.heads,
        layers=args.layers,
        use_pooled_phrase=not args.no_pooled_phrase,
    ).to(device)
    parameter_count = sum(p.numel() for p in model.parameters())
    print(f"parameters    : {parameter_count:,}")
    print(
        "bucket trunk  : "
        + ("phrase+context+token+param" if model.use_pooled_phrase else "context+token+param")
    )

    tensors.token_ids = tensors.token_ids.to(device)
    tensors.param_ids = tensors.param_ids.to(device)

    optimiser = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, T_max=args.epochs)

    valid_batch = tensors.batch(valid_rows, vocab)
    valid_batch = {key: value.to(device) for key, value in valid_batch.items()}

    history = []
    best_score = -1.0
    best_epoch = 0
    stale = 0

    for epoch in range(1, args.epochs + 1):
        model.train()
        order = list(range(len(train_rows)))
        random.shuffle(order)
        running = 0.0
        batches = 0

        for start in range(0, len(order), args.batch):
            chosen = [train_rows[i] for i in order[start : start + args.batch]]
            batch = tensors.batch(chosen, vocab)
            batch = {key: value.to(device) for key, value in batch.items()}

            optimiser.zero_grad()
            loss, parts = compute_loss(model, tensors, batch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimiser.step()

            running += loss.item()
            batches += 1

        schedule.step()

        metrics = evaluate(model, tensors, valid_batch)
        history.append({"epoch": epoch, "loss": running / max(1, batches), **metrics})

        # Exact match is the metric that matters: getting four of five abilities right
        # is a wrong loadout. Ties break on presence F1 so early epochs still move.
        score = metrics["exact_match"] + 0.001 * metrics["presence_f1"]
        marker = ""
        if score > best_score:
            best_score = score
            best_epoch = epoch
            stale = 0
            torch.save(
                {
                    "state_dict": model.state_dict(),
                    "vocab": vocab.words,
                    "config": {
                        "max_len": args.max_len,
                        "d_model": args.d_model,
                        "nhead": args.heads,
                        "layers": args.layers,
                        "use_pooled_phrase": not args.no_pooled_phrase,
                    },
                    "held_out_token": held_out,
                    "token_names": token_names,
                    "trained_at": {
                        "epoch": epoch,
                        "exact_match": metrics["exact_match"],
                    },
                },
                args.out,
            )
            marker = "  <- saved"
        else:
            stale += 1

        print(
            f"epoch {epoch:>3}  loss {running / max(1, batches):.4f}  "
            f"P {metrics['presence_precision']:.3f}  R {metrics['presence_recall']:.3f}  "
            f"presence-F1 {metrics['presence_f1']:.3f}  "
            f"bkt {metrics['bucket_accuracy']:.3f}  "
            f"moved {metrics['bucket_moved_accuracy']:.3f}  "
            f"dir {metrics['bucket_direction_accuracy']:.3f}  "
            f"exact {metrics['exact_match']:.3f}{marker}"
        )

        if stale >= args.patience:
            print(f"no improvement for {args.patience} epochs, stopping at {epoch}")
            break

    (HERE / "train_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"\nbest epoch {best_epoch}, checkpoint -> {args.out}")
    print(f"history  -> {HERE / 'train_history.json'}")


if __name__ == "__main__":
    main()
