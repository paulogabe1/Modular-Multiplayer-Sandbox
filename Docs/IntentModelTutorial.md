# The Intent Model — from zero to defensible

A companion to `Docs/BuildJournal.md`. That document is the game: networking, replication,
abilities, the test rig. This one is the other half — the small neural network that turns
`"make fireballs punchy but slow"` into ability tokens and parameter buckets.

Read it in order if you are learning this; jump to §7 if you only want to know what went
wrong and why the current numbers are what they are.

**One rule for the whole document:** every number is either *(Measured)* — produced by a
command in §9 you can re-run — or labelled a design decision. Nothing here is asserted from
memory.

---

# PART 0 — Why a model exists here at all

The obvious objection first, because it is the one an interviewer will ask: **the project
already had a working rule parser.** `Rules/RuleParserLibrary.cpp` lowercases the text and
substring-matches each token's name and keywords. It is fast, deterministic, needs no
dependencies, and on the current test corpus it scores **0.652** token-set exact against the
model's **0.480**. So why train anything?

Because keywords are not the problem. Keywords are already solved. Here is what the parser
structurally cannot do:

| Host types | What is required | Keyword parser | Model |
|---|---|---|---|
| `fireballs only` | one token, nothing else | works | works |
| `everything except invisibility` | the set complement | cannot — no negation exists | intended, **still fails** |
| `punchy but slow fireballs` | two parameters moving opposite ways | cannot — it can only select abilities | works at bucket level |
| `let me vanish twice as long` | a comparative → `Duration higher` | cannot — no notion of "twice" | works |
| a brand-new ability named by description | generalise past the vocabulary | only if the word is a keyword | intended via trigrams |

*(Design, then Measured in §8.)*

The distinction that matters: the parser is a **set selector**, and the brief asked for a
**slot filler**. Those are different jobs, and only one of them needs learning. That is the
honest justification, and it is also why the parser stays in the codebase permanently as the
fallback rather than being replaced wholesale.

Two constraints shaped everything downstream:

1. **The model only proposes.** Its output is a list of token names plus buckets. It never
   writes a gameplay value and never bypasses validation. `ULoadoutValidator::ValidateTokens`
   and `GetCategoryCap` remain the only gate — a hallucinated ability becomes the existing
   rejection message ("'X' is not an ability I know"), not a broken match.
2. **Validation stays in C++.** The model emits `"higher"`, never `40`. Arithmetic, clamping
   and range checks stay in code you can unit-test. This is also why the output space is
   small enough for a tiny model to learn at all.

---

# PART 1 — The environment is part of the model

This section exists because it cost real time and it will cost yours if you skip it.

| Thing | Value here | Why it matters |
|---|---|---|
| Interpreter | Python **3.12.13**, created by `uv` | PyTorch wheels lag new CPython releases; the system Python was 3.14 and had no wheel |
| Environment | project-local `.venv` | `pip install` into a system interpreter is how a project breaks unrelated software |
| `torch` | **2.15.0**, CPU wheel, from PyPI | ~120 MB, not the 2.5 GB CUDA build |
| GPU | RTX 3080 Laptop, 8 GB | **unused.** PyPI's Windows torch is CPU-only |
| Other deps | `fastapi`, `uvicorn` | serving only |

The GPU point is worth stating plainly rather than glossing: the code never touches the
card. For 7,700 short sentences and a ~200 MB model that is fine — an epoch is under a
minute on CPU — but do not tell someone you trained on a 3080. You did not. If you wanted
the GPU you would install the CUDA index build explicitly.

Setup, from the project root:

```bat
uv venv --python 3.12 .venv
.venv\Scripts\python.exe -m pip install torch fastapi uvicorn
```

**Lesson.** The interpreter version is a model dependency exactly like a library version.
Pin it the same way.

---

# PART 2 — Where the vocabulary comes from

The model must never invent an ability, so its labelled vocabulary is *derived* from the
asset you already author rather than maintained in parallel.

```
/Game/Sandbox/DA_AbilityTokens          (UPrimaryDataAsset, not a DataTable)
  └─ TMap<FName, FAbilityTokenDefinition> Tokens
       ├─ Keywords       → synonym pools the corpus is rendered from
       ├─ DefaultParams  → the "default" bucket's value
       └─ ParamRanges    → Min/Max, i.e. what "much_lower" and "max" clamp to

Tools/intent_model/catalogue.json       (exported, checked in)
```

Export once with `execute_unreal_python_readonly`, then never hand-write it. The table stays
the single source of truth; the model is downstream of your data.

Current catalogue — six tokens, of which **five train and one is held out**:

| Token | Category | Held out |
|---|---|---|
| Fireball | Offense | |
| LightningStrike | Offense | |
| Invisibility | Defense | |
| Shield | Defense | |
| SpeedBoost | Mobility | |
| **Blink** | Mobility | **yes** |

Holding `Blink` out of training is the zero-shot experiment: everything that mentions it is
scored as a token the model has never seen, which is the stand-in for "the host invents an
ability you did not ship".

---

# PART 3 — The output contract: buckets, not numbers

```json
{"tokens": [{"name": "Fireball", "params": {"Damage": "higher"}}]}
```

Five buckets, in this fixed order, because index identity is load-bearing:

| Index | Bucket | Becomes |
|---|---|---|
| 0 | `much_lower` | range **Min** |
| 1 | `lower` | **0.5 ×** default |
| 2 | `default` | default, untouched |
| 3 | `higher` | **2 ×** default |
| 4 | `max` | range **Max** |

`bucket_to_value(param_range, bucket)` in `schema.py` does that mapping and clamps; the C++
side then clamps again. Every call site does `.argmax(-1)` over `BUCKETS` order, so the
integer indices must never be reordered.

Three consequences worth internalising:

- **The model cannot produce an out-of-range number**, by construction. It has no continuous
  output to overflow.
- **"twice as hard" never has to be learned as arithmetic.** The model only has to learn
  *that the phrase is a comparative*, which is a classification problem. This is the single
  design decision that makes a small model viable.
- **`default` means "the phrase did not mention this parameter"**, not "the parameter is
  important". That reading is what makes the bucket metric meaningful — see §6.

The service returns `{"tokens": []}` when nothing clears the confidence floor. That empty
list is a *signal*, not an error: the caller falls back to the keyword parser.

---

# PART 4 — Generating the corpus instead of labelling it

There is no annotation step in this project. `generate_dataset.py` samples a structurally
valid intent, renders a natural sentence for it, and repeats:

```
sample intent  →  render through a template family  →  {"text": ..., "tokens": [...]}
```

The label comes *first*, so it is correct by construction. Ground truth cannot be wrong; only
the template variety can.

Families, and what each one is for:

| Family | Teaches | Split |
|---|---|---|
| `allow_terse` | `fireballs only` | train |
| `only_prefix` | `only fireballs` | train |
| `request_can_we` | `can we do fireballs` | train |
| `category_verb` | `more offense` | train |
| `exclude_everything_except` | complement | train |
| `exclude_no_terse` | complement | train |
| `param_single_reduce` | one parameter down | **valid** |
| `param_single_superlative` | `maximum damage` | **test** |
| `param_multi_agree` | two parameters, same direction | **test** |
| `param_multi_conflict` | two parameters, opposite | train |
| `exclude_all_but` / `exclude_without` | negation | **test** |
| `only_verb` | every token, nothing moves | **valid** |

Splitting by **whole family** rather than by row is deliberate: a random row split leaks the
template into both sides and reports a score the model cannot reproduce on new phrasing.

## The four generator bugs, and why they are in this tutorial

Each of these produced a corpus that trained fine and measured badly, and each was caught by
inspecting generated rows rather than by looking at a loss curve.

1. **Every complement sentence raised `KeyError`.** The renderer looked up each token's
   surface word in a map built only from tokens *present* in the label. `everything except
   invisibility` names a token that is absent, so the lookup missed. Symptom: 60,000/60,000
   attempts rejected, zero duplicates — a guard was failing, not the data.
2. **Pluralisation on verb-ish keywords** produced `rangeds` and `velocitys`.
3. **Category words aliased to abilities.** `defense` was a keyword of Shield, so
   `no defense` equipped invisibility. The parser has no negation, which is precisely the bug
   class the model is meant to fix.
4. **Label/sentence contradiction.** A parameter family rendered a sentence naming only
   Fireball but labelled Fireball *and* Invisibility. Identical text, two different labels — a
   row that cannot be learned, only averaged over.

The fix for all four was the same class of change: make the generator **audit its own
output**. Structural invariants (a `X only` sentence never carries a multi-token label; an
`everything except …` sentence must exclude something) plus a train/test **text-leak** check
run on every generation. A corpus you cannot assert things about is a corpus you will
misdiagnose later.

---

# PART 5 — The architecture, and the trick that makes zero-shot possible

Two towers, one shape:

```
phrase  → text encoder ─┐
                        ├→ presence score per candidate token → threshold → selected set
candidate token text ───┘
```

Nothing in the network knows how many abilities exist. The output width comes from the
catalogue at call time, which is what lets a sixth token appear without a retrain.

## The trigram trick

A brand-new ability's name is a word the model never saw. A plain word-level vocabulary
collapses it to `<unk>` and it can **never** match, so zero-shot is guaranteed to score zero.

So every word is emitted **twice**: once as its word token, once as its character trigrams.
`blink` survives as `bli`, `lin`, `ink` even if the word never appeared in training. That is
the only reason the held-out token has a chance rather than a certainty of failure.

## The presence head

The first version scored each candidate independently: `sigmoid(phrase · token)`. It cannot
express `everything except invisibility`, because that is a property of the **set**, and a
dot product is a property of a **pair**. Both `exclude_*` families scored exactly **0.000**
despite being training families.

The replacement is an MLP over five features per candidate:

```
[phrase vector, candidate vector, candidate-set mean, similarity, similarity − best]
```

`similarity − best` is the relative term that makes "except" expressible: a candidate can be
demoted for being *close to the best match* without any per-pair rule. *(Measured: this is
what moved `param_multi_agree` presence 0.337 → 0.642 and held-out detection 0.260 → 0.517.
It did not fix negation — see §8.)*

## The factorised bucket head

One five-way softmax asked a single question — "which bucket?" — and answered it badly. It is
now two questions trained on **disjoint row sets**:

```
bucket_trunk    Linear(4d→256) → GELU → Dropout → Linear(256→128) → GELU
moved_head      Linear(128, 1)     "does this parameter move at all?"
direction_head  Linear(128, 4)     much_lower / lower / higher / max
```

- `moved` is asked of **every** labelled row (plain BCE).
- `direction` is asked **only of rows that moved**; unchanged rows are masked out of the loss.
- `bucket_probs` reassembles a normalised `(n,5)`: `p(default) = 1 − P(moved)`,
  `p(bucket) = P(moved) · softmax(direction)`.

**Masking the rows, not weighting the classes.** That distinction is the whole of §6.

---

# PART 6 — The training loop, and why class weights are banned here

`BUCKETS[2]` (`default`) dominates the corpus. The obvious fix — inverse-frequency class
weights — was applied twice and deleted twice:

| Attempt | Exponent | What happened |
|---|---|---|
| 1 | `pow(0.5)` | rare buckets overweighted; model gambled on them and **traded correct `default`s away.** Bucket accuracy fell 0.832 → 0.654 across eight epochs, *below* always answering `default` |
| 2 | `pow(0.2)` | gentler; exact match up 34% relative, but the gamble remained visible as spurious parameters |
| 3 | **none** | masking replaced weighting. `default` is still the majority, and that is *correct* — the model should usually say it |

The reason weighting is the wrong tool: `default` is not an under-represented class, it is
**the true answer most of the time**. Weighting it down pays the model to invent movement. A
15%-positive task wants a well-calibrated `moved` head, not a rebalanced prior.

**The generalisable rule, and it appears twice in the journal: a weight you added to fix a
symptom is evidence of a measurement you got wrong.**

Two more loop details that are not style:

- **Early stopping patience.** `--patience 6` stopped the run at epoch 2 on a split that
  cannot distinguish good from bad. It is now `100`: run the cosine schedule out and pick the
  best epoch by exact match.
- **Log precision and recall separately.** F1 hides both failure modes: naming every ability
  gives recall 1.0 and precision 0.2; naming one safe ability gives the reverse. The old log
  printed only the blend, so the two looked identical.

---

# PART 7 — Measurement: four mistakes, in the order I made them

This is the most transferable part of the document. Every number in the model's history was
wrong at least once, and each time the fix was a better measurement rather than a better
model.

### Mistake 1 — selecting on an unrepresentative split

`valid.jsonl` contains only `only_verb` and `param_single_reduce`. Any bucket or direction
metric computed on it reads ~0.06 **regardless of skill**, because the label distribution
there is not the distribution the model trains on. Early stopping and checkpoint selection
both consumed that score. *(Measured: identical `dir 0.059` at epoch 1 for a weighted and an
unweighted run — proof the metric, not the model, was the constant.)*

**Fix:** select on `valid`, judge on `test.jsonl` and `golden.jsonl`. Never publish the split
you tuned on.

### Mistake 2 — one metric hiding two bugs

`intent_exact` requires the token set **and** every bucket to be right. A single missed bucket
and a spurious fourth ability zero the same row. Reading only that number said "the bucket
head has no direction skill", which was **false**.

The direct probe said otherwise: `twice as long` → `Duration higher` (correct), `punchy` →
`Damage higher` (correct). Every bucket error was *moving a parameter the phrase never
mentioned*. `evaluate.bucket_head_report` now scores `moved` and `direction` separately so
that cannot happen again.

**Fix:** when a blended metric says the model is broken, measure the specific thing before
redesigning anything.

### Mistake 3 — reporting a configuration nobody runs

`serve.py` ships `INTENT_CONFIDENCE=0.8`. `evaluate.py` defaulted to `--confidence 0.5`. So
every published figure described a floor that no request ever used. Same checkpoint:

| Floor | `test` token-set |
|---|---|
| 0.5 (what was published) | 0.473 |
| 0.8 (what actually ships) | **0.480** |

Small, but it means the number on the slide was not the number in the product. `evaluate.py`
now defaults to `0.8`.

**Fix:** evaluate at the configuration you ship, and let the tooling default to it so it
cannot drift.

### The one that was *not* a mistake — the threshold sweep

`bucket_probs` lets `default` win by `argmax` unless `P(moved) > 0.5`, and 0.5 is a
convention. Lowering it costs no retrain, so it had to be ruled in or out before spending
another 12 minutes of training. `Saved/sweep_moved.py` caches one forward pass and re-decodes
at every threshold:

| tau | `test` intent | `test` bucket | `golden` intent |
|---|---|---|---|
| 1.01 (all `default`) | **0.246** | **0.852** | **0.100** |
| 0.50 (current) | 0.245 | 0.835 | 0.100 |
| 0.40 | 0.243 | 0.819 | 0.100 |
| 0.30 | 0.232 | 0.791 | 0.100 |
| 0.20 | 0.216 | 0.757 | 0.100 |

Monotone, and running the wrong way. **No threshold beats doing nothing.** That closes the
cheap fix and moves the argument onto the data and the architecture, where it belongs.

*(A footnote that will otherwise look like a contradiction: the same sweep reported `test`
token-set 0.587. The sweep passed five token names; `evaluate.py` adds the held-out `Blink`
row back, and the presence head reads the candidate **set**, so a sixth row changes every
score. Both are correct for their own candidate set.)*

---

# PART 8 — Where it actually stands

> **This part is a moment in time, not the current state.** It records what was true when the
> baseline-versus-model comparison was first made. Nine further parts follow, each measured against
> the one before. The current shipped row lives in **PART 8.12 Step 9**: intent exact 0.756,
> token-set 0.881, bucket 0.950 on the same 537 live rows. If you are here to reproduce a number,
> take it from the part that produced it and treat this table as where the story starts.

All figures at the shipped confidence floor of 0.8. *(Measured — `evaluate.py`, §9.)*

| | token-set exact | intent exact | bucket accuracy |
|---|---|---|---|
| keyword baseline (the parser in the game) | **0.652** | **0.323** | 0.858 |
| intent model | 0.501 | 0.179 | 0.858 |

On `golden.jsonl` — 20 hand-written awkward phrases the generator never produces:

| | token-set exact | intent exact |
|---|---|---|
| keyword baseline | **0.700** | **0.250** |
| intent model | 0.500 | **0.200** |

**The model loses to the baseline on both corpora. That is the headline and it should not be
buried.** What it does win is structurally unavailable to the baseline:

| Family | keyword exact | model token-set | model bucket |
|---|---|---|---|
| `allow_terse` | 0.769 | 0.481 | 0.958 |
| `only_prefix` | 0.827 | 0.532 | 0.969 |
| `request_can_we` | 0.782 | 0.282 | 0.958 |
| `category_verb` | 0.500 | 0.583 | 1.000 |
| `exclude_all_but` | 0.000 | **0.000** | 0.954 |
| `exclude_without` | 0.000 | **0.000** | 0.949 |
| `param_multi_agree` | 0.000 | **0.000** | 0.324 |
| `param_single_superlative` | 0.000 | **0.000** | **0.613** |

Read the last four rows honestly. The baseline scores 0.000 on every parameter family because
it **cannot express a parameter at all** — the model is the only component that can. But it
also scores 0.000 on both `exclude_*` families, so negation is not a win either.

## What the corpus fix changed, and what it did not

The split used to pin the bucket. `param_single_comparative` (train) was always `higher`,
`param_single_reduce` (valid) was always `lower`, and `param_single_superlative` (test) was
always `max` — so the only clean single-parameter direction the model ever trained on was
`higher`, and it duly answered `higher` for every parameter of every phrase. The bucket is now
drawn from the prior in all three splits, and the split varies the *wording* instead.

*(Measured. The corpus was regenerated, so row contents are not identical between checkpoints —
see the comparing-runs caveat in `README.md`. The structural metrics below are the ones that
count, because each carries its own constant baseline computed on the same rows.)*

| Metric | Before the fix | After | Constant baseline |
|---|---|---|---|
| direction (which way?) | 0.326 | **0.637** | 0.447 |
| moved (did it move?) | 0.814 | 0.852 | 0.858 |
| `param_single_superlative` bucket | 0.524 | **0.613** | 0.638 |
| `param_multi_agree` token-set | 0.690 | **0.850** | — |

Direction went from **0.77×** its constant to **1.42×** it. That is the first time any part of
the bucket head has shown skill rather than a bias, and it was predicted before the run: if the
corpus were the binding constraint, direction should move toward or past its baseline *without
any change to the model*. It did.

## What is still broken, with causes

1. **`moved` is still a hair below a constant** — 0.852 against 0.858. The corpus fix did not
   touch this head, which is the evidence that *when to abstain* is a separate problem from
   *which way to go*.
2. **`param_single_superlative` bucket is 0.613 against a 0.638 majority.** `max` is now
   *reachable* — it was 0.01 before, even on the literal phrase `maximum damage` — but not yet
   better than guessing. It still receives only about 15% of single-parameter rows.
3. **Cause B — per-parameter polarity is now the top visible defect.** The confusions cluster:
   `Cooldown` truth `lower` or `max` read as `higher`, and `ProjectileSpeed` `lower`/`higher`
   swapped. "Slower" is `higher` for `Cooldown` and `lower` for `ProjectileSpeed`. Concatenating
   two L2-normalised vectors does not hand the head that product cheaply; the architectural
   answer is attention from the parameter vector over the phrase's token sequence.
4. **Negation still 0.000** in the corpus, even though the *served* complement phrase works at
   model level (four of five candidates at 0.98–0.99, the excluded one at 0.005).
5. **Out-of-domain input is confidently wrong**, and worse than earlier reported. Measured on the
   P14 checkpoint at the 0.8 floor: `make me a sandwich` returns **SpeedBoost at 1.000**, and
   `what is the weather tomorrow` returns **four abilities at 0.87–0.99**. There is no abstain path,
   and the confidence floor is not a substitute for one — the head is not uncertain, it is wrong and
   certain.

   Related: complement handling is surface-form bound, not general. `everything except fireball` and
   `no shield` are handled correctly (excluded token at 0.001), but `give me everything but
   invisibility` and `all abilities except lightning` return *only* the excluded token at 0.97–0.99.
   Both defects are the same shape — the model treats template vocabulary (`make`, `give me`, `but`)
   as evidence about which ability is meant, because it has never seen a phrase where that vocabulary
   was the only signal.
6. **Held-out `Blink` regressed, and it is not calibration.** Detection 0.493 → 0.286 at 0.216 FPR.
   A threshold-free AUC settles what a single floor cannot: **0.687 → 0.581**, with the whole
   detection/FPR curve sitting below P12's at every threshold. That is a real loss of separation.

   The control says it is *specific to the held-out token*. Per-candidate presence AUC on
   `test.jsonl`: `Fireball` 0.674 → 0.787, `LightningStrike` 0.702 → 0.767, `Invisibility`
   0.761 → 0.745, `Shield` 0.765 → 0.713, `SpeedBoost` 0.808 → 0.742. Presence overall did not
   degrade — it improved on two candidates. Only the path that generalises *past* the training
   vocabulary got worse, which is the trigram channel.

   Most likely mechanism, not yet proven by ablation: the new frames are more formulaic
   (`max damage fireball`, `less damage on fireball`), so presence can be solved by recognising
   the template surface instead of by character similarity to the candidate name. In-vocabulary
   accuracy rising while zero-shot falls is exactly what that trade looks like.

## Attention: what P14 changed

The bucket head used to receive the phrase as `peak` — one max-pooled vector — concatenated with
the parameter name. Max pooling picks the strongest word *without knowing which parameter is being
asked about*, so the word "slower" arrived identical whether the question was `Cooldown` or
`ProjectileSpeed`. The head could only memorise pairings, and for the one it did not memorise it
guessed. That is the whole polarity defect, and it was an input-shape problem, not a data gap.

P14 gives the parameter name a say in *which words get read*: the parameter embedding is the query
in a small cross-attention over the phrase's per-token hidden states, and the bucket head receives
that context where `peak` used to be. One `nn.MultiheadAttention`, same width out, so nothing
downstream changed shape. The full walkthrough is in the build journal under **P14**.

| Metric | P13 | **P14** | Constant baseline |
|---|---|---|---|
| direction | 0.637 | **0.675** | 0.447 |
| `moved` | 0.852 | **0.869** | 0.858 |
| bucket accuracy | 0.858 | **0.879** | 0.858 |
| intent exact (`test`) | 0.179 | **0.234** | 0.323 |
| token-set exact (`test`) | 0.501 | 0.485 | 0.652 |
| intent exact (`golden`) | 0.200 | **0.300** | 0.250 |

`moved` clears its constant for the first time, and the model now beats the keyword baseline on
`golden.jsonl` intent exact — 0.300 against 0.250. Twenty rows is a small sample, and it still loses
on `test.jsonl` intent exact (0.234 against 0.323), so the parser stays the primary path.

## Abstention: P15, and the measurement bug that had to be fixed first

The model had never seen a phrase that named nothing. Every training row mentioned at least one
candidate, so template vocabulary — `make`, `give me`, `reduce`, `higher` — was always accompanied
by a real referent. The presence head therefore learned to read those words as evidence about
*which* ability was meant. `make me a sandwich` came back as SpeedBoost at confidence 1.000 not
because the model was confused, but because nothing had ever told it that an answer of "nothing"
exists.

The fix is data, not architecture: families whose correct label is the **empty set**. Two of them,
because they cost very different amounts.

- `abstain_ood` puts a request frame around something outside the game (`make me a pizza`). It
  shares no vocabulary with a real request, so it is cheap to learn and it carries the case a
  player will actually hit.
- `abstain_template` deliberately reuses the real verbs and intensity adjectives on non-abilities
  (`make the music louder`). It is maximally confusable with a real request, which is exactly what
  makes it effective — and exactly what makes it expensive.

Two call sites had to be told an empty label is legal. `validate_selection` rejects it, correctly,
because there is nothing to equip, so `generate()` skips that check for these families and
`audit_split()` inverts the invariant instead: an abstain row carrying a label is the bug. The
trainer needed no change at all — `Tensors` already builds an all-zero presence row for a label with
no tokens, and `compute_loss` already masks rows with no bucket targets. The contract already
supported abstention; only the data did not.

### The measurement bug that invalidated the first attempt

P15 ran both families at 9% of rows. It worked on its own metric — both failing phrases silent at
0.000 — and it appeared to cost a great deal: token-set exact 0.485 → 0.421, and `moved` 0.869 →
0.810, below its own 0.852 constant.

That number was not trustworthy, and finding out why was worth more than the abstain work itself.
All three splits were drawing from **one seeded random stream**. Adding a training family consumed
draws, which shifted the state every later split read, so the test sentences themselves changed.
The control run — same generator with the abstain families switched off — reproduced the *P14*
corpus byte for byte rather than the current one, which is how the bug announced itself: its test
baseline came back 0.652/0.323, P14's numbers exactly, when the current corpus should have given
different ones.

So the comparison was between two different exams, not two different students. The generator now
seeds each split independently (`args.seed`, `+1`, `+2`, `+3`, plus a matching `random.seed` for the
one function that draws from the module RNG). Verified by hashing `test.jsonl` after generating
with and without the abstain families: identical. The test split is now invariant to anything done
to training, which is what makes every number below readable.

### What P16 ships

Three changes, each with a reason:

1. **Per-split RNG streams**, so the test set stops moving.
2. **A lopsided allocation**: the cheap family gets the volume (3.6%), the expensive confusable one
   is held to 1.8%, for 5.3% total instead of 9%.
3. **An additive budget** (`--train 8440`, 8,143 rows). P15's negatives *displaced* real labelled
   rows — about 7,319 against the control's 7,714 — so part of its "cost" was simply less
   supervision.

Measured against the control (identical generator, abstain families off, same stable test set):

| Metric (`test`) | control, no abstain | **P16** |
|---|---|---|
| intent exact | 0.236 | **0.241** |
| token-set exact | 0.479 | 0.437 |
| bucket accuracy | 0.876 | 0.872 |
| `moved` | 0.874 | 0.823 |
| direction | 0.668 | **0.673** |
| intent exact (`golden`) | 0.300 | **0.300** (baseline 0.250) |
| bucket accuracy (`golden`) | 0.839 | **0.871** |

And on the thing it was built for: **8 of 10** held-out distractors abstain, both phrases that
failed at P14 return nothing at 0.000, and **6 of 6** control requests still resolve.

`moved` is the remaining cost, and it is the one metric still below its constant on `test`
(0.823 against 0.858). On `golden` it clears its own constant, 0.860 against 0.839, so the deficit
is specific to the generated test families rather than the head being broken. Restoring it without
giving up abstention is the open problem.

**The transferable lesson, twice over.** First: a change has to be priced against the metrics it
was not aimed at — abstention was the top-ranked defect and removing it still made the model worse.
Second, and more embarrassing: before comparing two runs, prove the two runs were scored on the
same data. A shared RNG stream made every comparison in this project silently self-referential, and
it took a control run producing P14's numbers to notice.

## What would fix it, ranked

1. **`moved` on `test`**, 0.823 against a 0.858 constant, while `golden` clears its own. Something
   about the generated test families makes the head over-predict movement. It is the last metric
   where the model loses to a trivial predictor, and it survived five training runs.
2. **Restore lexical variety in the training frames**, to recover the zero-shot channel the P13
   corpus fix cost (`Blink` AUC 0.687 → 0.581, while in-vocabulary presence improved).
3. **The token-set gap**, 0.437 against the baseline's 0.664 — the families that fail are the
   `exclude_*` complements, which the baseline cannot even express, so this is a specific open
   problem rather than a general weakness. P16 still returns the excluded token for the
   `but` / `all abilities except` phrasings.
4. **The C++ HTTP transport**, so any of this becomes playable instead of merely measured.

---

# PART 8.5 - One word, two meanings: the `faster` collision (P20 / P21)

Part 7 left `param_multi_agree` weak and blamed a "vocabulary collision" in prose. This part is
what happens when you stop describing a collision and go count it. The method generalises to any
suspicion of the form "the model is confusing X with Y".

## Step 1 - Count the collision before touching the corpus

The suspicion was that `faster` was pulling in `SpeedBoost`. A suspicion is not a diagnosis and it
is not a fix. So the first artefact is a read-only audit, `Saved\analyze_collision.py`, which asks
the corpus two questions:

1. Of every training row containing a speed adjective, **what share actually labels `SpeedBoost`?**
2. Per word, how does the label split?

Answer on the P19 corpus (7,788 rows):

```
rows containing a speed adjective : 1495 (19.2%)
  SpeedBoost LABELLED             :  820
  SpeedBoost ABSENT               :  675

  faster   present 122   absent 164
  fast     present 115   absent  98
  quicker  present 137   absent 134
  speedy   present 110   absent  55
```

`faster` is right 43% of the time. A feature that is right 43% of the time carries no information,
and a model that uses it anyway is guessing. The second query is what explains the guessing:

```
rows where a speed adjective looks like the ability NAME, with SpeedBoost labelled:
  quicker 76 | fast 68 | speedy 66 | quick 66 | hasty 64 | faster 60
```

Sixty training rows use the word `faster` **as `SpeedBoost`'s name**. Not as an adjective - as the
noun standing in for the ability. That is not an accident of sampling: `catalogue.json` lists
`faster` and `quicker` among `SpeedBoost`'s keywords, and `build_alias_maps` (~line 306) promotes
any keyword not shared with another token into a surface name.

So the corpus held two contradictory lessons about one word:

- `param_multi_together` rendered `faster` as Fireball's `ProjectileSpeed`, `SpeedBoost` absent.
- `pick_words` rendered `faster` as `SpeedBoost` itself, present.

## Step 2 - Fix the right layer

The tempting fix is to delete `faster` from `SpeedBoost`'s keywords. **Reject it.** Players say
"make my speed boost faster" and that is a correct request; deleting the word removes a real
feature to win a metric.

The second tempting fix is to make `build_alias_maps` refuse adjectives. Also rejected, and this
one is a methodology point: `pick_words` feeds **every** family, including the test split. Changing
it rewrites `test.jsonl` and destroys comparability with every checkpoint already measured. The
guard rail for this project is that the test split is invariant to training changes, and it held
across the whole session:

```
test.jsonl         = 6734c287c5447c2312cdde2c5d33bfdec2f8b0e654bf1e0b1aea6743474fb4fa
test_heldout.jsonl = a9735b40270e6f586edd38fb87e9584db13b62bc38498b8a124a53812731bc46
```

So the change had to be **additive**: a new training family that supplies the missing lesson
without editing one existing row. New families go to `train`, never to `test`, and each split
seeds from its own stream (`args.seed`, `+1`, `+2`, `+3`), which is *why* a train-only addition
cannot move a test hash.

## Step 3 - Teach the noun, not the adjective

The real insight is that the ambiguity is not in the adjective. It is that the adjective was the
*only* cue in those sentences. Put a noun beside it and the noun decides.

`fam_param_speed_contrast` (`generate_dataset.py`, ~line 818) renders **both halves from one
distribution**:

| phrase | label |
|---|---|
| `make the fireball fast` | Fireball, `ProjectileSpeed` `higher` |
| `give me quick speed boosts` | SpeedBoost, `Multiplier` `higher` |
| `make the speed boost blazing fast` | SpeedBoost, `Multiplier` `max` |
| `fireballs that are fast` | Fireball, `ProjectileSpeed` `higher` |

Teaching only the Fireball half would fix the failing sentence and **break**
`make the speed boost faster`, which works today. Rendering the pair is what forces presence to
key on the noun rather than on the adjective.

Two details that mattered in practice:

- **Verb frames must not name abilities by alias.** `pick_words` hands back catalogue keywords,
  and several of Fireball's are verbs (`shoot`, `fireballing`, `blast`), so the first build
  produced `the shoot should be quick`. Fixed with `_name_word` (~line 756): display name only.
- **Read the generated rows before training.** Never train on a renderer you have not eyeballed.

## Step 4 - Probe the phrases, not the averages

An aggregate metric cannot tell you whether a collision is fixed, because fixing one phrase and
breaking another nets out to roughly zero. `Tools\intent_model\probe_phrases.py` prints the exact
phrases from any checkpoint:

```bat
cd Tools\intent_model
..\..\.venv\Scripts\python.exe probe_phrases.py model_p19_backup.pt model.pt
```

P19 scored **4 of 13**. P20 scored **8 of 13**, and the whole speed cluster flipped:

| phrase | P19 | P20 |
|---|---|---|
| `stronger and faster fireballs` | SpeedBoost, nothing | **Fireball**, nothing |
| `faster fireballs` | Fireball, nothing | **Fireball, ProjectileSpeed higher** |
| `slower fireballs` | Fireball, nothing | **Fireball, ProjectileSpeed lower** |
| `make the speed boost faster` | SpeedBoost, nothing | **SpeedBoost, Multiplier higher** |
| `give me quick speed boosts` | SpeedBoost, nothing | **SpeedBoost, Multiplier higher** |

Aggregates moved the same way, all four up on the live 3-token surface:

| live 3-token | P19 | P20 |
|---|---|---|
| intent exact | 0.313 | **0.326** |
| token set exact | 0.544 | **0.575** |
| bucket accuracy | 0.859 | **0.864** |
| bucket acc, token ok | 0.835 | **0.839** |
| `param_multi_agree` exact | 0.082 | **0.173** |

## Step 5 - Read your own regressions

P20 also **lost** a phrase it used to get right:

```
make the fireball punchy but slow
  want  Fireball Damage higher + Cooldown higher
  P19   OK
  P20   Fireball ProjectileSpeed lower + Cooldown lower   <- MISS
```

The new family was the cause, and the mechanism is the same one being fixed. It taught
`slow` as a *downward* `ProjectileSpeed` word on Fireball. `param_multi_conflict` renders
`punchy but slow fireballs` where `slow` means `Cooldown` **up** on that same ability. One word,
now three meanings.

The guard is explicit (`fam_param_speed_contrast`, prepare branch): withhold the downward branch
wherever a bare `slow` is already spoken for.

```python
if {"Damage", "Cooldown"} <= set(params):
    bucket = rng.choices(["higher", "max"], weights=[0.8, 0.2])[0]
else:
    bucket = rng.choices(
        ["much_lower", "lower", "higher", "max"], weights=[0.05, 0.2, 0.6, 0.15]
    )[0]
```

A second, quieter regression was budget. `allocate` splits a split's rows across its families by
weight, so any new family shrinks every other one: `param_multi_together` fell 901 -> 824 rows.
Since two-parameter phrases are the entire point of this track, the two `param_multi_*` weights
were raised to restore their absolute volume (1.4 -> 1.55 and 1.2 -> 1.35), and the new family was
dropped to 0.5 because its surface saturates at roughly 250-310 distinct sentences - at weight 1.0
it asked for 687 rows and produced 312 after 41,220 duplicate rejections.

## Step 6 - Reject your own fix when the evidence says so

Two attempts to recover `punchy but slow` both made things worse, and the second is the
instructive one because it was a *single-variable* experiment.

| attempt | what changed | probes | live intent exact |
|---|---|---|---|
| P20 | the speed family, no guard | **8/13** | **0.326** |
| P21 | guard + three weight changes | 6/13 | 0.272 |
| P22 | guard alone | 6/13 | 0.263 |

P22 was worse than P20 by more than P21 was, which rules out "the weights confounded it". The
guard itself is the damage: it inverts `make the speed boost faster` to `Multiplier LOWER` and
costs `slower fireballs` outright.

So the guard was reverted, the corpus regenerated back to the P20 state, and P20 shipped. That
last step matters more than it looks: **if the corpus on disk does not match the shipped
checkpoint, the checkpoint is not reproducible**, and every future comparison starts from an
unknown baseline. Verify it with row counts (`train 7,433`, `param_speed_contrast 312`) and the
test hashes, then confirm `model.pt` is byte-identical to the backup you promoted.

Two habits from this whole episode, both cheap:

- **Train experiments to a different `--out`.** `train.py --out model_p22.pt` means a rejected
experiment can never destroy the shipped weights. P14 and P18 were both lost to overwrites in
this project.
- **Score the phrases before you trust the average.** Every real conclusion here came from
  `probe_phrases.py`, and every misleading one came from a valid-split number.

**The transferable rule:** a family's weight is not "how important is this family", it is "how many
rows does this family need to cover its surface". A narrow family given a large weight spends the
budget on its own duplicate rejections and shrinks everyone else's.

---

# PART 8.6 — A check that cannot fail: the equip request (P23)

This chapter is about the defect that shipped for three checkpoints because nobody typed the
simplest possible sentence into the game.

## Step 1 — Read the game log before the model card

The PIE log had this line, and it is the whole story:

```
Intent resolved Invisibility.Duration: lower -> 2.500
```

Nobody asked for a shorter invisibility. They typed the ability's name to **equip** it. So go
and type the simple thing at the service, six times:

```
.venv\Scripts\python.exe Saved\probe_service.py "invisibility" "fireball" "speed boost" "invisible" "stealth" "cloak"
```

```
'invisibility' -> {'Invisibility': {}}          correct
'fireball'     -> {'Fireball': {'ProjectileSpeed': 'lower'}}   2500 -> 1250 for free
'speed boost'  -> {'SpeedBoost': {}}            correct
'invisible'    -> {'Invisibility': {'Duration': 'lower'}}       5s -> 2.5s
'stealth'      -> {'Invisibility': {'Cooldown': 'lower'}}
'cloak'        -> {'Invisibility': {}}          correct
```

Three of six. Read the pattern, not just the count: P19 answered `invisibility` wrongly and
`fireball` correctly; P20 swapped them exactly. **A rule the model actually held would not
swap.** That is the difference between mis-weighted and missing, and it tells you a third
weight nudge will not work.

## Step 2 — Count the concept before you touch the corpus

Do not guess why. Write a read-only script and count what the corpus teaches about the case that
is failing. `Saved/analyze_equip_only.py` asks one question -- how many train rows name exactly
one ability and leave every parameter alone:

```
train rows                 : 7433
single-ability rows        : 5076 (68.3%)
  ... all params default   : 1108 (14.9%)   <- the equip-only concept
```

1,108 rows exist, so the concept is not absent. The number that matters is the next one:

```
per family (single-ability rows / of which all-default):
  param_multi_conflict            961       0
  param_single_comparative        961       0
  param_multi_together            824       0
  param_single_max                687       0
  param_speed_contrast            312       0
  only_suffix                     343     343
  request_want                    164     127
```

**Every one of the 3,745 `param_*` single-ability rows moves a parameter, and every one of them
says so with an intensity word.** The equip rows that do exist are mostly `only`/`except`
phrasing, not plain naming. So the plain shape was thin *and* outnumbered 3.4 to 1. That is a
corpus gap you can point at, not a hunch.

## Step 3 — Make the label structural, not a hope

A new family can be wrong in a very quiet way: render an equip sentence and accidentally give it
a moved parameter. Avoid the possibility instead of testing for it. `mentioned_count=0` means no
parameter is driven, so `sample_buckets` returns `default` for all of them and the branch only
narrows the token set:

```python
if family_name == "equip_only":
    s = sample_factory(rng, mentioned_count=0)
    name = rng.choice(s.tokens)
    params = list(param_ranges(CURRENT_TOKENS[name]))
    s.tokens = [name]
    s.buckets = {name: {p: DEFAULT_BUCKET for p in params}}
    s.extra["equip_word"] = _equip_word(rng, name)
    return s
```

The label is now correct by construction. Twelve framed templates do the wording
(`{word} please`, `the {word}`, `i want {word}`, `equip {word}`, `add {word}`, `put {word} on`,
...), and the name comes from a filtered pool -- see Step 4.

## Step 4 — Guard the collision you just spent a checkpoint removing

P20 existed to remove one word carrying two meanings: `faster` was both SpeedBoost's name and a
speed cue. An equip family can rebuild that collision backwards by using `speedy` as a bare name
while `speedy` also means "raise this". So the pool is the token's aliases minus every word that
`CURATED_BARE` or `SPEED_ADJECTIVES` uses as a bucket cue:

```python
def _equip_word(rng, token_name: str) -> str:
    pool = [
        alias for alias in CURRENT_ALIASES.get(token_name, [token_name])
        if alias.lower() not in SPEED_ADJECTIVE_WORDS
    ]
    return rng.choice(pool) if pool else token_name
```

Verified across all six tokens by `Saved/check_equip_words.py`:

```
SpeedBoost
  usable names (12): SpeedBoost, Speed Boost, speed, speedy, boost, haste, hasty, sprint, ...
  correctly excluded as bucket cues : fast, faster, quick, quicker
LIVE COLLISIONS (must be 0)          : 0
```

## Step 5 — A check whose loop can be skipped is not a check

The first run of that script printed `collisions remaining: 0` and **nothing else**. The
per-token block never printed, because `CURRENT_TOKENS` is empty until `configure()` is called
and the loop body never executed. It reported success for the same reason it would have reported
anything: the loop was empty.

Every check needs a witness that the thing it is checking actually ran:

```python
if not g.CURRENT_TOKENS:
    raise SystemExit("CURRENT_TOKENS is empty at import -- any '0 collisions' "
                     "result would be a false pass.")
```

This is the same class of mistake as judging a model on `valid.jsonl`: measuring something that
cannot fail tells you nothing while looking like it told you everything.

## Step 6 — Do not let the new family leak into the test split

Adding the bare frame (`{word}`) pushed the generator's train/test overlap from **0 to 36**:
`allow_terse` is a test family that renders exactly a bare alias, so the two splits intersected on
essentially the whole alias pool. Training on those strings turns `allow_terse` into a
memorisation check and silently inflates the score you were going to compare with.

The frame was dropped. Bare names stay a genuine generalization test -- and if they still fail
afterwards, *that* is the evidence for accepting overlap on purpose. Not a guess made in advance.

```
corpus guard: train 7433 -> 7492
test.jsonl        6734c287...  unchanged
test_heldout.jsonl a9735b40...  unchanged
audit problems 0 / train-test overlap 0 / train-valid overlap 0
```

## Step 7 — Accept the tradeoff, out loud

A new family divides the same budget, so every other family shrank: `param_multi_conflict`
961 -> 900, `param_multi_together` 824 -> 771, `param_single_max` 687 -> 643. That dilution is
exactly what cost `more duration and more multiplier` when the contrast family landed in P20 --
except this time the dilution landed in that family's favour and the phrase came back.

| metric | P20 | P23 |
|---|---|---|
| live intent exact | 0.326 | **0.363** |
| live bucket accuracy | 0.864 | **0.882** |
| test token set exact | 0.472 | **0.524** |
| test intent exact | 0.249 | **0.297** |
| gap to keyword baseline | -0.065 | **-0.017** |
| probes | 11/19 | **15/19** |

One phrase was lost, and it is named rather than averaged away: `slower fireballs` now returns
nothing. `punchy but slow` improved to Damage-higher + ProjectileSpeed-higher but is still wrong,
which keeps it in the binding column, not the vocabulary column.

**The transferable rule:** when a behaviour is wrong on most inputs but right on some, the concept
is missing from the corpus, not mis-weighted in the model -- and the fix is counted data with a
label that is correct by construction, not a third attempt at the weights.

---

# PART 8.7 — Out of surface, not out of budget (P24)

## Step 1 — Your first plausible number will be true and irrelevant

After P23 shipped, exactly one phrase regressed: `slower fireballs` returned nothing. The first
thing worth counting is the obvious suspect — the corpus's overall bias:

```
non-default labels : 5829
  upward   (higher/max)       : 4014 (68.9%)
  downward (lower/much_lower) : 1815 (31.1%)
  ratio up:down               : 2.21 : 1
```

And it is worst in precisely the multi-parameter families: `param_multi_conflict` 32% down,
`param_multi_together` 29% down. That is a satisfying story — the model raises things because the
data raises things — and it is **wrong**.

Ask what the correct label actually is before you attribute a failure to a bias. `punchy but slow`
should be Damage `higher` **and** Cooldown `higher`. A slow cooldown is one you wait longer for,
which is a *raise*. There is no buff/nerf confusion in that sentence at all, so a valence
imbalance cannot be its cause.

## Step 2 — Look for a word that carries two numbers

The curated adjective table is where a collision hides in plain sight:

```python
"ProjectileSpeed": {"lower":  ["slower", "slow"]},
"Cooldown":        {"higher": ["slower", "slow", "longer"]},
```

The same word, two opposite numeric directions. A slow projectile is a smaller number; a slow cast
is a larger one. Both are correct English — only the **parameter** can decide. This is the P20
`faster` collision in a form nobody had fixed yet.

## Step 3 — Check whether the family that teaches it can even grow

The generator reports its own shortfall every run. Read that block instead of theorising:

```
families that could not reach their target:
  param_speed_contrast   wanted  643, got  312 (attempts 38580, dup 38268, ...)
```

38,268 rejections out of 38,580 attempts is not bad luck — it means the family has **no unused
combinations left**. Its space is 2 token/parameter pairs x 12 adjectives x 12 frames, about 288
sentences. A family with no unused sentences cannot grow no matter what weight you give it; extra
weight just spends attempts on duplicates.

That distinction — *out of budget* versus *out of surface* — decides which lever you have.

## Step 4 — Widen the surface at constant weight

Add nouns and frames. Do not add a family, and do not raise the weight. Adding a family divides
everyone's quota (the dilution that cost `more duration and more multiplier` in P20); a saturated
family raising its own realized count at the same weight costs nobody anything.

```python
SPEED_NOUNS = {
    "Fireball":   (["fireballs", "shots", "blasts"], ["fireball", "shot", "blast"]),
    "SpeedBoost": (["speed boosts", "boosts", "sprints"], ["speed boost", "boost", "sprint"]),
}
```

| | before | after |
|---|---|---|
| `param_speed_contrast` realized | 312 | **643** |
| downward rows in it | 120 | **194** |
| train rows | 7,492 | 7,823 |

Plural and singular are listed separately because the frames inflect ("that are", "the ...");
mixing them produces slips like "the fireballs that are slower". Check afterwards that
`param_multi_conflict` and `param_multi_together` are unchanged — they draw from the stream
*before* this family, so they must be, and that is what makes the comparison clean.

## Step 5 — Know your probe noise floor before you panic

Probes went **15/19 -> 14/19**. The target phrase was fixed; two others were lost. But both of
those live in families whose rows are byte-identical between the two runs, so the flips are
retraining stochasticity, not data — at n=19 the band is roughly two phrases.

Meanwhile the aggregates moved by +0.028 and +0.029 over 1,100+ rows.

**A phrase that can flip when the data it depends on did not change is not evidence on its own.**
Probes tell you *which* behaviour to look at. The aggregate tells you whether you improved.

## Step 6 — Name the lines you actually crossed

| metric | P23 | P24 |
|---|---|---|
| live intent exact | 0.363 | **0.391** |
| test intent exact | 0.297 | **0.326** (baseline 0.314) |
| test `moved` | 0.853 | **0.883** (constant 0.858) |
| test `direction` | 0.657 | **0.699** (constant 0.453) |

Two thresholds for the first time: better than a trivial predictor at deciding *whether* a
parameter moves, and better than the keyword parser on the hard split rather than merely close.

## Step 7 — Say what is still broken, in one sentence

Three phrases still fail identically — `punchy but slow`, `strong and fast`, `quicker cooldowns and
a bigger multiplier` — and each needs a word bound to a parameter the sentence never names. That is
the architecture question, not a data question, and it is now isolated to a sharper test case than
it had before.

**The transferable rule:** when a family misses its quota, the duplicate-rejection count tells you
whether you have a budget problem or a surface problem. Widening the surface is free; adding a
family is borrowed from everyone else.

---

# PART 8.8 — The ablation that explained without winning (P25)

## Step 1 — Read the head before you redesign it

The tempting move when binding fails is to propose a new mechanism. Read the existing one first:

```python
def attend(self, states, key_padding_mask, param):
    context, _ = self.param_attention(
        param.unsqueeze(1), states, states, key_padding_mask=key_padding_mask
    )
    return F.normalize(self.attention_norm(context.squeeze(1)), dim=-1)
```

The parameter name is the **query** over the phrase's per-token states. That is already
parameter-conditioned attention — the exact thing you were about to propose. So the mechanism is
not missing. Ask instead what could let the model *ignore* it:

```python
hidden = self.bucket_trunk(torch.cat([phrase, context, token, param], dim=-1))
#                                              ^^^^^^ parameter-agnostic
```

`phrase` is mean-pooled. It carries the whole sentence's meaning and knows nothing about which
parameter is being scored. With it in the same vector, the head can answer "the sentence says
*slower*" and never look at `context`.

## Step 2 — State the prediction before running it

If the shortcut is real, withholding `phrase` should make the binding appear, and should cost
something. Write both down first — a hypothesis that only predicts the good half is not testable.

## Step 3 — Change exactly one thing

`--no-pooled-phrase` drops one column from the trunk: `[context, token, param]`. Parameter count
1,477,639 -> 1,444,871, a difference of 32,768 = one d_model column, which is how you confirm nothing
else moved. `phrase` still feeds `presence_logits`, so token detection is untouched by construction.

## Step 4 — It bound

| phrase | P24 | P25 |
|---|---|---|
| `punchy but slow` | ProjectileSpeed lower | Damage higher + **Cooldown higher** + ProjectileSpeed lower |
| `more duration and more multiplier` | Duration higher only | Duration max + **Multiplier higher** |

Cooldown `higher` on the first phrase is a label four checkpoints had never produced. The mechanism
was never absent — a shortcut was starving it.

## Step 5 — And it lost on precision

Best exact 0.860 -> 0.837, `direction` 0.726 -> 0.662, and the phrase now over-fires to **three**
parameters instead of one. The pooled phrase is what supplies general intensity ("maximum", "a
bit"); attention alone does not carry it as reliably.

## Step 6 — Reject it anyway, and say why

Ship P24. A change that trades precision for binding and loses every aggregate is not an improvement
just because it fixed the phrase you were staring at. Probes stayed 14/19 across both, which is why
the aggregate decides.

## Step 7 — Take the diagnosis, not the checkpoint

The useful output is a sharper statement of the problem:

> Binding works when the shortcut is removed. The cost is precision, because one vector is asked to
> do two jobs — carry the sentence's overall intensity **and** say which parameter a word belongs to.

That reframes the next lever as data rather than architecture. Which is also why the obvious
follow-up — a split trunk (`moved` keeps the phrase, `direction` gets context-only) — was *not*
run: P25 already measures direction-without-phrase at 0.662, so that variant predicts roughly a
tie. **A change whose predicted outcome is a tie is churn, and churn is how P21 and P22 were wasted.**

---

# PART 8.9 — The biggest error was not the model's job (P26)

## Step 1 — Decompose the error before spending another cycle

P25 closed the architecture branch and named the next lever as data. Before acting on that, ask a
narrower question than "what is the score": **on the set that ships, what shape are the misses?**

`Saved/audit_live_failures.py` scores the 3-token set row by row and sorts each miss into four kinds
(token set wrong, missing parameter, wrong direction, spurious parameter), then totals by family.
The result is the whole point of the exercise:

| family | missed / rows |
|---|---|
| `exclude_all_but` | 73 / 73 |
| `exclude_without` | 65 / 65 |
| `param_multi_agree` | 69 / 98 |
| `request_can_we` | 35 / 65 |

Two families are 138 of 327 misses — 42% of all error — and neither was the thing five consecutive
training cycles had been aimed at. Binding was the third-largest problem, not the first.

## Step 2 — Check the mundane explanation before the interesting one

Before concluding "structural", rule out "the words are new to it". `Saved/audit_live_oov.py` builds
the train+valid word set and asks how often a hit and a miss contain a word that set never had:

```
hit   rows  210   containing an unseen word:   16 (8%)
miss  rows  327   containing an unseen word:   31 (9%)
```

Essentially identical. So a bigger vocabulary would not have helped, and the corpus was not the lever.
Counting that took one script and killed a plausible wrong plan.

## Step 3 — Read what the label actually is

```python
def fam_exclude_without(rng, s: Sample) -> str:
    excluded = [n for n in CURRENT_CANDIDATES if n not in s.tokens]
    return f"without {join_natural([s.words[n] for n in excluded])}"
```

The sentence names the abilities to **remove**; the label is everything left. That is set
subtraction. Both the model and the substring parser read forward and equip what the sentence names,
which is why both sat at exactly 0.000.

## Step 4 — Ask whether it is a learning problem at all

It is not. A rule that is exact belongs outside the weights. `Tools/intent_model/exclusion.py` runs
in `serve.py` **before** the model:

```python
if "but" in words and ("all" in words or "everything" in words):
    return "but"        # not bare `but`: "punchy but slow" excludes nothing
```

The guard matters as much as the rule. Bare `but` joins two adjectives in `param_multi_conflict`, so
requiring `all`/`everything` in the same sentence keeps the rule off every non-exclusion phrase. It
also refuses to fire unless at least one candidate word resolves, so `without breaking the game`
falls through to the model, and it emits every bucket as `default`, so it cannot invent a parameter
change.

## Step 5 — Measure the shipped path, not the checkpoint

| live 3-token (537 rows) | token-set exact | intent exact | bucket accuracy |
|---|---|---|---|
| keyword baseline | 0.339 | 0.128 | 0.797 |
| model alone (P24) | 0.594 | 0.391 | 0.888 |
| shipped (rule + model) | **0.838** | **0.585** | **0.888** |

*This is the P26 path -- the exclusion rule and the checkpoint, nothing else -- and it is what was
measured when this step was written. PART 8.11 adds `clauses.py` on top of it and PART 8.12 Step 9
carries the current row. Every table in this document is the measurement of its own part; do not
read an early one as today's number.*

Fired on 131 rows, right on 104, **zero false fires** outside the two families. Note which column did
not move — bucket accuracy is identical, because the rule never emits a bucket. 0.194 of intent exact
for no training and no new weights, against +0.028 then −0.023 from the two preceding cycles.

`eval_live.py` now prints the checkpoint row and the shipped row separately. Keep them separate: only
one of them is a fact about the weights.

## Step 6 — When a number looks broken, debug it instead of explaining it

On the 6-token benchmark the rule scored **0/312**. That is the *shape* of a broken rule, so it got
debugged rather than rationalised. `Saved/debug_exclusion_6token.py` printed the resolved excluded
set next to the label, and every row differed by exactly one token: `Blink`. It is the held-out
token, so test labels never contain it — and the exclusion families never *name* it either, because
the generator only gives a word to non-forced candidates. Removing Blink from the candidate list gave
**246/312**.

## Step 7 — Read the gap as a defect in the data

312 − 246 = 66, and there are exactly 66 exclusion rows whose label demands a bucket nothing in the
sentence expresses:

```
'all abilities but the shoot, bubble and sprint'  ->  Invisibility Cooldown lower
```

No parser recovers that, so 246/312 is 100% of what the benchmark permits and the rule has no headroom
left. The fix is a train-only change — those families should emit all-`default` buckets — recorded
rather than made, because the test split is frozen and a train change cannot move a number.

**The rule to carry forward:** decompose error by *cause* before another cycle. Here 42% of it was a
compositional rule no amount of training would learn, and the headline hid it by averaging it in with
binding failures that had already consumed five cycles. Set subtraction, unit conversion and table
lookups belong outside the weights — where they cost nothing and cannot be unlearned by a retrain.

---

# PART 8.10 — Counting support before spending a cycle (P27)

## Step 1 — Pick the worst family the rule cannot reach

P26 moved 42% of the error out of the weights. What is left at the top is `param_multi_agree`: 69 of
98 live rows missed, bucket accuracy 0.664. Its shape is one sentence naming two different parameters:

```
shorter cooldowns and heavier fireballs
```

`cooldowns` is the noun for the first clause, `fireballs` is the token, and the second adjective sits
between them. The model has to keep two adjective-to-parameter bindings alive at once and attach both
to a noun that arrives last.

## Step 2 — Assume the cheap explanation, then test it

It looks lexical: maybe the corpus rarely puts `heavier` next to `Damage higher`, so the weights never
spend capacity on it. P24 was exactly that story and produced one of the strongest cycles of the
track, so the temptation is to run the same play.

`Saved/audit_multi_agree_support.py` asks, per row and per non-default clause, whether *any* training
row pairs that parameter and bucket with that adjective. 187 test rows, 374 non-default clauses,
**0 starved**. Every pairing the test split asks for is in the corpus. P24's defect does not repeat.

## Step 3 — Then measure how much support, not just whether

`Saved/audit_adjective_support.py` counts training rows per sanctioned
`(parameter, bucket, adjective)`:

| triple | training rows |
|---|---|
| `Damage higher` / `harder` | 36 |
| `Damage higher` / `stronger` | 29 |
| `Damage higher` / `heavier` | 16 |
| `Damage lower` / `softer` | 11 |
| `ProjectileSpeed lower` / `slower projectiles` | 6 |

Two are thin, and the fix looks like one line: widen `CURATED_INTENSITY`, the adjective pool these
families draw from.

## Step 4 — Check that the fix is legal before writing it

It is not, and this is the step that saves the cycle. `CURATED_INTENSITY` is read by
`fam_param_multi_agree` while generating the **test** split, so widening it changes the adjective pool
the frozen split renders from. The split hashes move (`6734c287...`, `a9735b40...`) and every measured
number in P23, P24 and P26 stops being comparable.

The only pool that can be touched is `_bare_adjective` — exactly one caller, `param_multi_together`, a
train-only family — and it would lift the thinnest pairing from 11 to roughly 25. That is a
redistribution inside the training set. It adds no new way for a sentence to mean something.

## Step 5 — Decide on the shape of the failure, not the size of the number

Two probes from `Saved/probe_phrases.py` say what the defect actually is:

```
make the fireball strong and fast
  -> ProjectileSpeed higher        (Damage dropped at the join)

give the speed boost quicker cooldowns and a bigger multiplier
  -> Cooldown higher               (should be lower -- the other clause's direction)
```

One clause survives and the other is lost, or the wrong clause's direction lands on the right
parameter. Both on adjectives that are perfectly well supported. That is binding across a clause
boundary, and more rows of `softer` do not change what the model has to *do* with a sentence. P24
earned its cycle by moving a family from out-of-surface to in-surface; this would not.

## Step 6 — Ship nothing, and prove nothing moved

**Decision: no retrain.** `model.pt` stays P24, MD5 `3eb0c428402a9691eddaf206f91985d2`, 7,823 train
rows. The probe re-ran at **14 of 19**, identical to P24, with the same five misses:

```
stronger and faster fireballs
make the fireball strong and fast
make the fireball punchy but slow
more duration and more multiplier
quicker cooldowns and a bigger multiplier
```

At n=19 the noise band is plus or minus 2, so 14 proves little on its own. What it proves is that a
change which should have been a no-op was one — a checkpoint discipline check, not a result.

**The rule to carry forward:** a change whose predicted outcome is a tie is churn. P21 and P22 were
both that pattern and both lost ground. Count the support the failing rows need before paying for a
retrain; if the pairings exist and the failure is in how the sentence is composed, the lever is
structural — and P25 already measured one structural variant at −0.064 on direction.

---

# PART 8.11 — Two clauses, one rule (P29)

PART 8.10 ended with a decision and no code: `param_multi_agree` fails because the model cannot hold
two adjective-to-parameter bindings at once, the pairings it needs are all present in the corpus, and
another retrain would be churn. This part is what replaced that cycle.

## Step 1 — Change the target, not the score

Up to here the target was `test.jsonl`, which is 1,166 generated template rows. Getting from 0.585 to
0.75 on those means winning two-clause binding in a 1.5M-parameter encoder — and P21, P22 and P25 all
tried and lost ground.

The thing you actually care about is smaller and clearer: a host types a phrase and the game equips
the right abilities. That is six sentences, and three of them work. So the target becomes "make those
phrases right", the score becomes a way of checking you did not break anything else, and the training
track freezes at P24.

## Step 2 — Write the thing that is exact

The label space is a handful of parameter names and five buckets, and the surface words for both are
a closed list. That means a sentence like

```
shorter cooldowns and heavier fireballs
```

is resolvable without any learning at all: split on `and`, resolve each half to one
`(parameter, bucket)`, and if you get two *different* parameters, that is the answer.

`Tools/intent_model/clauses.py` does exactly that, and it is deliberately paranoid. It answers only
when the evidence is complete — two distinct parameters, every direction word placed, exactly one
ability named or exactly one ability whose parameters fit. Anything less and it returns `None` and
the model keeps the phrase. That is the same contract `exclusion.py` has: a rule that fires on
phrases it only half understands is worse than no rule at all, because it removes the chance for the
model to get them right.

It emits token names and bucket labels. It never emits a number, so `ULoadoutValidator` is untouched.

## Step 3 — Measure it before believing it

`Tools/intent_model/measure_clauses.py` reports fire count, precision, every wrong row, and the
shipped path composed with the exclusion rule and the model:

```bat
cd Tools\intent_model && ..\..\.venv\Scripts\python.exe measure_clauses.py
```

The first run was the useful one, because it failed:

```
clause rule   fired 21 rows, right on 6  (0.286)
    param_multi_agree  6/21
```

0.286 is worse than the model's 0.306 on the same family. Four of the six probe phrases never reached
the rule at all. That is the moment to stop tuning and go and look.

## Step 4 — Find out why, one clause at a time

A throwaway script printed, per phrase, the words, the exclusion marker, which candidates were named,
the ability's parameters, and each clause's resolution. It found three separate defects, in order of
damage:

**1. Plurals.** `named: []` for `shorter cooldowns and heavier fireballs`. The family renders
`noun_form(name)`, so the corpus says `fireballs`, and whole-word alias matching was looking for
`fireball`. The rule had no ability to work with on most rows.

**2. Adjectives were naming abilities.** For `quicker cooldowns and a bigger multiplier` the output was
`named: ['SpeedBoost'] matched ['quicker']`. `catalogue.json` lists `fast`, `faster` and `quicker` as
SpeedBoost keywords — the same words a player uses to ask for more projectile speed. The project had
already measured this: 122 SpeedBoost rows against 164 non-SpeedBoost rows, which is why
`SPEED_ADJECTIVES` exists in the generator. A word present half the time cannot carry presence.

**3. A parameter the ability does not have.** Probe 6 then resolved Fireball instead, because
`a bigger multiplier` named no Fireball parameter, so the clause fell through to the bare adjective
`bigger` and matched Fireball's Damage. A clause that names a parameter this ability lacks is
evidence the phrase is not about this ability — not a reason to guess another parameter.

## Step 5 — The three fixes

- Singularise both sides before comparing (`fireballs` -> `fireball`).
- Drop every alias that is *entirely* direction words before naming an ability, so `fast`, `faster`
  and `quicker` can never name SpeedBoost.
- Refuse a phrase for an ability when a clause names a parameter that ability does not have.

With those, the ability is identified by **which parameter set the clauses fit**, not by a keyword.
`quicker cooldowns and a bigger multiplier` names no ability at all, and only SpeedBoost has both
Cooldown and Multiplier — so SpeedBoost is the answer, and no keyword decided it.

Re-measured:

```
clause rule   fired 46 rows, right on 44  (0.957)
    param_multi_agree  44/46
```

## Step 6 — Read the two remaining misses instead of guessing

`measure_clauses.py` prints every wrong row with `got` and `want`, which turned two guesses into two
facts:

```
MISS slower cooldowns and as long as possible speed boosts
     got  Duration=higher     want Duration=max
MISS briefer and a smaller boost speed boosts
     got  Duration=much_lower want Duration=lower
```

`as long as possible` is a multi-word superlative that contains `long`, which reads as `higher` on its
own; it now has its own table, checked before the single-word lexicon and longest-first so
`twice as long` cannot be shadowed. And `briefer` is Duration **lower** in `CURATED_INTENSITY` — the
lexicon had it one bucket too strong.

```
clause rule   fired 46 rows, right on 46  (1.000)
```

## Step 7 — Wire it in behind the rule that was already there

`serve.py`, immediately after the exclusion block and before `predict`:

```python
clauses = apply_clauses(request.text, catalogue, token_names)
if clauses is not None:
    return {"tokens": [
        {"name": entry["name"], "params": entry["params"]} for entry in clauses["tokens"]
    ]}
```

`eval_live.py` composes the same three layers so the shipped number stays measurable:

```
exclusion fired 131 rows, right on 104  (0.794)
clause rule fired  46 rows, right on  46 (1.000)
overlap between the two rules: 0 rows
```

| live 3-token, 537 rows | keyword | model alone | shipped P26 | shipped P29 |
|---|---|---|---|---|
| intent exact | 0.128 | 0.391 | 0.585 | **0.650** |
| token set exact | 0.339 | 0.594 | 0.823 | **0.847** |
| bucket accuracy | 0.797 | 0.888 | 0.886 | **0.913** |

Checkpoint unchanged, corpus unchanged, both test-split hashes untouched. Every point of that gain is
code, not weights.

## Step 8 — Over HTTP, then in the game

Over HTTP first, because it isolates the service from the client — `Saved/verify_http_clauses.py`
posts the same body `WishCraftPlayerController` builds:

```
punchy but slow fireballs   -> Fireball[Damage=higher ProjectileSpeed=lower Cooldown=default]
```

Then in game. Restart the service (`start "WishCraft intent" /min cmd /c ...`), confirm
`curl -s localhost:8077/health`, start PIE, and drive `SubmitRuleText` on the server PC with
`has_authority()` **and** `is_local_player_controller()`. Read the result out of the editor log —
`LogSandbox: Intent resolved ...` is the validator's own output:

| phrase | validator output | P28 output for the same phrase |
|---|---|---|
| `punchy but slow fireballs` | Damage `higher` 40, ProjectileSpeed `lower` 1250, Cooldown `default` 1.5 | Damage `higher` 40, ProjectileSpeed `default`, Cooldown `higher` 3.0 |
| `make the fireball strong and fast` | Damage `higher` 40, ProjectileSpeed `higher` 5000, Cooldown `default` | Damage `default` 20, ProjectileSpeed `higher` 5000 |
| `give the speed boost quicker cooldowns and a bigger multiplier` | Duration `default`, Multiplier `higher` 3.0, Cooldown `lower` 3.0 | Duration `default`, Multiplier `default`, Cooldown `higher` 12.0 |
| `maximum damage and shorter cooldowns fireballs` | Damage `max` 100, ProjectileSpeed `default` 2500, Cooldown `lower` 0.75 | not previously run |

Two practical notes. `SubmitRuleText` takes an `FString&` out-parameter, which does **not** come back
as a second return value in Python — do not try to unpack two values. And the request is async on the
player controller, so submit in one tool call and read the result in the next; a Python `sleep` would
block the game thread the completion callback needs.

The fourth phrase exists only to prove the `max` and `lower` buckets survive `ValidateIntentJson`;
P28 had only ever sent `higher` and `default`.

## Step 9 — What this cost, and what is still true

The rule fires on 46 of the 98 rows in its family. The other 52 still go to the model, and pushing
coverage higher means adding lexicon words, each one a small precision risk. 46/46 is the bar, and
the miss list is the only thing worth editing.

It is also a third hand-maintained surface table, and unlike the generator's tables it carries an
opinion — `fast` on Fireball is ProjectileSpeed, on SpeedBoost it is Multiplier — which is why it is
its own table rather than derived from `CURATED_INTENSITY`.

And one thing was still invisible: every layer logged `Rules applied (intent model)`, including when
a rule answered and the model was never consulted. The editor log could not tell you which layer
produced a loadout. That is fixed in **PART 8.13**.

---

# PART 8.12 — The rule was refusing on its own dictionary (P30)

PART 8.11 ended at 46 fired out of 98 rows in its own family, right on all 46. That read like a
finished job. It was a rule that had solved a quarter of a family and a report that could not show
the rest.

This part is about the measurement that hid half the family, and the three defects underneath it.

### Step 1 — A rule is never wrong if you only count what it answers

Precision 1.000 sounds like the work is done. Coverage 46 of 98 says the other 52 rows went to the
model, and the model got 18 of them — 0.346. The rule was not the finished answer to this family; it
was the finished answer to the easy half of it.

The reason nobody could see that is in `measure_clauses.py`: `misses()` prints rows a rule answered
**wrongly**. A row the rule never touched is neither fired nor wrong, so it is never printed. Half
the family was invisible in every report I had.

### Step 2 — Measure the refusals, not the errors

The fix is not a change, it is a script. For every row the rule did not fire on, print which
abilities it named and what each clause resolved to — or which guard refused. That is `_debug_coverage.py`,
written against the rule's own internals and deleted once it had said what it had to say:

```
SILENT much longer and the longest cooldowns invisibilities
       named=[]
       Invisibility ['Duration', 'Cooldown'] 'much longer'->Duration=higher 'the longest cooldowns invisibilities'->Cooldown=max
SILENT longer cooldowns and punchier fireballs
       named=['Fireball']
       Fireball ['Damage', 'ProjectileSpeed', 'Cooldown'] 'longer cooldowns'->REFUSE 'punchier fireballs'->Damage=higher
SILENT maximum projectile speed and longer cooldowns fireballs
       named=['Fireball', 'SpeedBoost']
```

Three different failures, and the first line already says the rule is right and the dictionary is
wrong: Invisibility resolves both clauses perfectly and is not being *named*.

### Step 3 — Read the plural before you read the model

`named=[]` on a sentence that ends in `invisibilities`. The corpus renders `noun_form(name)`, and
`pluralise` turns a y-ending display name into `-ies`. The rule singularises both sides before
comparing — but `_singular` only stripped a trailing `s`:

```
_singular("invisibilities")  ->  "invisibilitie"     # matches no keyword on earth
```

So the token was never identified, the phrase was refused, and the model got another row it could
not read either. The fix is four characters of intent:

```
if len(word) > 4 and word.endswith("ies"):
    return f"{word[:-3]}y"          # invisibilities -> invisibility
```

This is the second time this family punished me for the same assumption, and PART 8.11's first defect
was the `-s` half of it. If the corpus pluralises, the rule has to un-pluralise *properly*, not
approximately.

### Step 4 — Then the one word with no reading for the parameter it was next to

`longer cooldowns` -> REFUSE, in about twenty rows. `BARE["longer"]` carried Duration and Range
readings only. When a clause names a parameter explicitly, the rule looks for a reading **for that
parameter** and refuses if there is none — which is correct behaviour, given a correct dictionary.
The dictionary was missing the obvious reading: a longer cooldown is a Cooldown that went **up**.

```
"long":   [("Duration", "higher"), ("Range", "higher"), ("Cooldown", "higher")],
"longer": [("Duration", "higher"), ("Range", "higher"), ("Cooldown", "higher")],
```

Order carries the meaning: Duration still wins wherever a token has one, so `much longer` on
Invisibility is still Duration, and the Cooldown reading only applies where it is the reading that
fits.

First measurement, from 46 fired:

```
clause rule   fired 83 rows, right on 83  (1.000)
shipped       intent exact 0.650 -> 0.695
```

No precision lost. That is the signature of a defect, not a tradeoff: a correct dictionary entry
adds rows without moving the ones already right.

### Step 5 — When the sentence names two abilities, let the parameters break the tie

`maximum projectile speed and longer cooldowns fireballs` named **two** abilities, because `speed`
is a SpeedBoost keyword and `projectile speed` is also the name of Fireball's parameter. The rule's
guard was "never guess between two names" — refuse. Nine rows sat behind it.

But the rule already had a better test, used when *no* ability is named: the ability is whichever one
the clauses fit. Nothing about two names makes that test illegal. If exactly one of the named
candidates resolves cleanly, that one is the subject; if both or neither do, refuse.

```
clause rule   fired 98 rows, right on 98  (1.000)
              param_multi_agree 98/98
shipped       intent exact 0.695 -> 0.713
```

Every row of the family, with the weights untouched.

### Step 6 — Then notice it was never only a two-clause rule

`param_single_superlative` is 110 rows at 0.645, and its shape is `shoot with the maximum damage`: no
conjunction, one ability, one parameter, everything else `default`. That is the *same arithmetic* the
rule already performs — `_resolve_for` fills every unmentioned parameter with `default`, which is
exactly what the label wants. The only thing stopping it was a guard demanding two parameters, which
exists because a two-clause phrase with one explained clause is genuinely unfinished.

With no conjunction there is no second clause to be unfinished. One resolved parameter is then
complete evidence:

```
single = len(CLAUSE_SPLIT.split(text)) == 1
return _resolve_for(text, catalogue, named[0], 1 if single else 2)
```

```
clause rule   fired 198 rows, right on 185  (0.934)
              param_single_superlative 90/97
shipped       intent exact 0.713 -> 0.750
```

185 of 198 is no longer 1.000, so the next step is reading what the thirteen are.

### Step 7 — Reject the fix that sounds like a principle

Thirteen misses, and they cluster. Seven are one generator template (below). The other six are bare
adjectives after a named ability: `invisibility quicker`, `the biggest boost fast`, `barely any
speed fast`.

The tempting fix is a gate: require an explicit parameter word before accepting a single clause,
because "the biggest boost fast" names an ability and an adjective and nothing else. It reads like
sound policy. Measured, it was a rule that threw away the good with the bad:

| | fired | right | shipped intent exact |
|---|---|---|---|
| no gate | 198 | 185 | **0.750** |
| explicit-word gate | 174 | 167 | 0.734 |

The gate avoided 6 wrong rows and took **18 right ones** with it. Bare adjectives after a named
ability do carry evidence — they are just a dictionary problem here, not a policy problem. Reverted,
and the two actual gaps filled in instead: `biggest` and `maximum speed` are Multiplier **max**,
`barely any speed` is Multiplier **much_lower**.

The same reading fixed the remaining shapes: `more of a boost` / `less of a boost` are Multiplier
higher/lower, `forever` is Duration **max**, and `twice` is **higher** against whichever parameter
the clause named.

```
clause rule   fired 198 rows, right on 188  (0.949)
shipped       intent exact 0.756   token set 0.881   bucket 0.950
```

### Step 8 — In the game, one phrase per call

Four phrases the rule could not reach before P30, run through `SubmitRuleText` →
`ValidateIntentJson`:

| phrase | validator output |
|---|---|
| `maximum projectile speed and longer cooldowns fireballs` | Damage `default` 20, ProjectileSpeed `max` 5000, Cooldown `higher` 3.0 |
| `shoot with the maximum damage` | Damage `max` 100, ProjectileSpeed `default` 2500, Cooldown `default` 1.5 |
| `the longest cooldowns and maximum speed speed boosts` | Duration `default` 5.0, Multiplier `max` 3.0, Cooldown `max` 30.0 |
| `less of a boost and briefer speed boosts` | Duration `lower` 2.5, Multiplier `lower` 1.05, Cooldown `default` 6.0 |

All four finished `applied` and replicated to the client.

One thing cost me a confusing minute and is worth writing down: **submit one phrase per tool call.**
The controller serialises in-flight rule requests, so four phrases submitted in a single Python call
send only the first, and the other three silently do nothing — no error, because nothing failed.
If a phrase seems to vanish, check that its `Intent request started` line exists before you go
looking at the rule.

### Step 9 — Name the residue, because it is the honest part of the number

Ten rows are answered wrongly. Seven are a generator template, not a rule defect:

```python
# generate_dataset.py:659 -- used for BOTH the `lower` and `much_lower` bucket
options = [f"make the {p} on {word} lower"]
```

A `much_lower` row therefore renders word-for-word identically to a `lower` row, and no rule can
separate two sentences that are the same sentence. It is recorded and not fixed, because the test
split is frozen and repairing the generator would invalidate every number in P23 onward. This is the
same class of defect as the 66 mislabelled exclusion rows in Defect 2b.

The other three — `invisibility quicker`, `invisible quicker`, `hide fast` — want **two** abilities
from a phrase that names one. The model misses all three as well, so firing costs nothing and buys
nothing; it is left standing rather than special-cased.

**What this part is actually about.** The rule did not get better at language. It got better at
reading its own tables: a plural it could not undo, a parameter with no reading, and a tie it
refused to break with evidence it already had. Precision fell from 1.000 to 0.949 doing it, and that
drop bought 34 net correct rows. Coverage of a family is a number you have to measure on purpose —
a rule that never speaks is 100% precise and useless, and a report that only prints errors will call
it perfect.

---

# PART 8.13 — Make the log say which layer answered (P31)

PART 8.12 ended with the clause rule answering 198 of 537 live rows. That left an uncomfortable
hole: the game could not tell you *which* of its three layers had produced the loadout you were
looking at. Every path logged the same words.

This part is tiny in code and worth reading anyway, because the interesting part is not the code.
It is that the obvious fix breaks the game, and the reason it breaks is the same invariant that has
been holding the whole pipeline together.

## Step 1 — The symptom, stated exactly

The shipped path is three layers in order:

```
exclusion.apply_exclusion(...)   # 131 live rows
  -> clauses.apply_clauses(...)  # 198 live rows
    -> predict(model, vocab, ...)  # the rest
```

And all three ended with the same line in `WishCraft.log`:

```
Intent request finished (applied): Rules applied (intent model).
```

So when a phrase produced a wrong loadout, the first question — *did a rule answer this, or did the
model?* — had no answer from the log. You had to re-post the phrase to `/rules` by hand with curl
and hope the service gave the same answer the game had received. With two thirds of some families
going through a rule, that is not a corner case; it is the normal debugging path.

## Step 2 — Why the one-line fix breaks the game

The obvious change is one field:

```python
return {"tokens": [...], "source": "clauses"}    # do not do this
```

That breaks rule application outright, and the reason is in two places that were written for
different purposes:

| guard | where | what a second key does to it |
|---|---|---|
| `Root->Values.Num() == 1` | `WishCraftPlayerController.cpp:204` | it is the test that distinguishes the empty-list *abstain* sentinel from a real answer; it stops matching |
| `Root->Values.Num() != 1` | `LoadoutValidator.cpp:144` | it rejects the whole response: *"The model returned an invalid rules response. No rules were changed."* |
| `Object->Values.Num() != 2` | `LoadoutValidator.cpp:163` | one level deeper, every ability entry must hold exactly `name` and `params`; a third key there is rejected as *"Each model ability must contain only a name and parameter buckets."* |

The C++ guard is guarding a contract, not being fussy: the validator refuses anything it cannot
interpret *completely*, because a half-applied loadout is worse than no loadout. So a `source` key
in the body does not make the log better — it makes every successful rule application fail.

The per-entry guard at `:163` is the same discipline one level deeper, and it closes the loophole you
will reach for next: there is no "one extra key per ability" escape either. The whole shape is fixed
at `{"tokens": [{"name": ..., "params": {...}}]}`, which is why metadata has to travel outside the
payload entirely rather than somewhere clever inside it.

This is why P29, which discovered the gap, wrote it down instead of shipping it.

## Step 3 — Put it in a header instead

HTTP already has somewhere to put metadata that is not the payload. `serve.py` only needed the
response object:

```python
from fastapi import FastAPI, Response

@app.post("/rules")
def rules(request: RuleRequest, response: Response) -> dict:
```

Then one line at each of the four exits:

```python
if not token_names:
    response.headers["X-Intent-Source"] = "no-catalogue"
    return {"tokens": []}

# ... "exclusion" after apply_exclusion answers
# ... "clauses"   after apply_clauses answers
# ... "model"     on the fall-through
```

The body never changes. That is the whole design: the JSON stays byte-identical, so neither guard
above is touched, and no numeric value gains a new way to be written.

## Step 4 — Read it where the response is actually valid

The header is only readable inside the completion callback, which is exactly where the body is read.

**The one line that silently returns nothing.** UE caches response headers in a
`TMap<FString, FString>` and `FHttpResponseCommon::GetHeader`
(`Engine/Source/Runtime/Online/HTTP/Private/GenericPlatform/HttpResponseCommon.cpp:76`) does a plain
`Headers.Find(HeaderName)` — a **case-sensitive** lookup. uvicorn/Starlette emit header names
**lowercased**, so on the wire it is `x-intent-source`. Asking for the canonical spelling returns an
empty string, with no error and no warning:

```cpp
// WRONG against this server: always empty. Every response logs the header-absent branch.
const FString Source = Response->GetHeader(TEXT("X-Intent-Source"));
```

This cost a full debug cycle, and its symptom is worth memorising because it does not look like a
bug: the game logs a perfectly healthy `Rules applied (intent model).` with the layer missing, while
`verify_intent_source.py` prints that same layer correctly from the same service. **When the probe
and the game disagree, suspect the reader, not the writer.** Read both spellings:

```cpp
FString Source = Response->GetHeader(TEXT("X-Intent-Source"));
if (Source.IsEmpty())
{
    Source = Response->GetHeader(TEXT("x-intent-source"));
}
```

and it is folded into the one message the player controller already produced:

```cpp
FinishRuleRequest(bApplied, bApplied
    ? (Source.IsEmpty()
        ? FString(TEXT("Rules applied (intent model)."))
        : FString::Printf(TEXT("Rules applied (intent model: %s)."), *Source))
    : FString::Printf(TEXT("Rules rejected: %s"), *Error));
```

The empty-string branch is deliberate. An absent header is a legal state — an older service build,
a proxy that strips it, or a client that reads only one spelling — and it must degrade to the old
line, not to `(intent model: ).` It is also why a missing layer name is *information*: it says the
client did not receive or did not find the header, which is a different bug from a wrong layer name.

## Step 5 — Prove the Python side without the game

Never start with PIE for a change like this. `Saved/verify_intent_source.py` posts the same body the
controller builds and prints the header next to the token names:

```
clauses       | expected clauses (two clauses) | shorter cooldowns and heavier fireballs -> Fireball
clauses       | expected clauses (one clause)  | shoot with the maximum damage -> Fireball
exclusion     | expected exclusion              | without the invisibility -> Fireball, SpeedBoost
clauses       | expected clauses (one clause)  | make the fireballs punchier -> Fireball
```

One detail that wastes time if you get it wrong: the request body only needs range **names**, not
numeric ranges.

```python
"ranges": {name: {} for name in token["ranges"]},
```

Numbers are the game's business — `ULoadoutValidator` is still the only thing that turns a bucket
into a value. A probe that sends the real ranges looks more faithful and teaches you nothing.

The fourth row is worth a sentence, because I first read it as a failure: `make the fireballs
punchier` names an ability and one adjective, which is precisely the P30 single-clause path. The
label in the script said "model or empty" and the rule said `clauses`. The label was wrong. Fix the
label, not the rule.

## Step 6 — Then prove the game side, one phrase per call

Restart the service (uvicorn runs **without** `--reload`, so a `serve.py` edit is invisible until you
do), confirm `/health`, Live-Code the C++, then in PIE:

```
Intent request started: shoot with the maximum damage
Intent resolved Fireball.Damage: max -> 100.000
Intent resolved Fireball.ProjectileSpeed: default -> 2500.000
Intent resolved Fireball.Cooldown: default -> 1.500
Intent request finished (applied): Rules applied (intent model: clauses).
```

That last line is the entire feature: the loadout names the layer that produced it, and it cost one
header and one `Printf`.

And the old rule still applies — **one phrase per tool call.** The controller serialises in-flight
requests, so a batch sends only the first.

### The console is not the player's path — click the panel

`RulesModel` is a developer shortcut. A player types into `WBP_RuleInput` and presses **Send**, and
that graph does more than the console does: it casts `GetOwningPlayer()` to `WishCraftPlayerController`,
gates on `HasAuthority && IsLocalController` (failing to *"Only the host can edit rules."*), binds
`OnRuleResolution` so the status line can report the outcome, and only then calls `SubmitRuleText`.
The controller puts the game into `FInputModeGameAndUI` with the cursor shown, so the panel is a
UI-mode overlay rather than something that has to be focused.

That path is verified end to end. Typing `shorter cooldowns and heavier fireballs` and pressing Send
produced exactly the clause reading, in the log and on screen:

```
Intent request started: shorter cooldowns and heavier fireballs
Intent resolved Fireball.Damage: higher -> 40.000
Intent resolved Fireball.ProjectileSpeed: default -> 2500.000
Intent resolved Fireball.Cooldown: lower -> 0.750
Intent request finished (applied): Rules applied (intent model: clauses).
```

and `StatusText` shows `Rules applied (intent model: clauses).` in green — the same attribution the
log carries, in front of the player.

**Do not conclude the button is broken from automation.** A synthetic Slate press against this HUD
registers the press-down and then reports the release *unhandled*, with no `OnClicked` and a silent
log — reproducibly, whether the press lands on the `Send` label or on the bare `SButton`. That is a
capture artefact of synthetic input, not a wiring fault, and it is easy to spend an hour "fixing" a
button that already works. One real click settles it in a second.

## Step 7 — The log has four terminal states, not one

`X-Intent-Source` is only half of what the finished line can say. `FinishRuleRequest`
(`WishCraftPlayerController.cpp:240`) is the single place that writes the line, and three different
callers reach it, so the same slot carries a layer, a fallback reason, or an error:

| what `WishCraft.log` says | who answered | what it means |
|---|---|---|
| `Rules applied (intent model: exclusion).` | `exclusion.py` | the phrase carried an exclusion marker |
| `Rules applied (intent model: clauses).` | `clauses.py` | an exact two-clause or single-clause reading |
| `Rules applied (intent model: model).` | the P24 checkpoint | no rule claimed the phrase |
| `Rules applied (intent model).` | unknown | **the header did not arrive** — a service older than P31, a proxy that stripped it, or a client reading only one spelling of the name (Step 4). The loadout is real; the attribution is not |
| `Rules applied (keyword fallback: model service unavailable).` | the keyword parser | connection failed, or the response was not HTTP 200 |
| `Rules applied (keyword fallback: model request could not start).` | the keyword parser | `ProcessRequest()` itself returned false |
| `Rules applied (keyword fallback: model abstained).` | the keyword parser | the service returned an empty token list |
| `Rules rejected: <error>` | `ULoadoutValidator` | a real refusal; the message names the stage |

Two traps live in that table.

**`no-catalogue` never appears in the game log.** `serve.py:132` sets that header and returns
`{"tokens": []}`, and the controller tests for the empty list *before* it does anything with the
header:

```cpp
if (Json.Len() <= 65536 && FJsonSerializer::Deserialize(Reader, Root) && Root.IsValid()
    && Root->Values.Num() == 1 && Root->TryGetArrayField(TEXT("tokens"), Entries) && Entries->IsEmpty())
{
    ApplyKeywordFallback(RuleText, TEXT("model abstained"));
    return;
}
```

So the header the service just set is discarded and the game reports `model abstained` instead.
`no-catalogue` is visible from the Python probe only. If you are staring at the game log waiting for
it, you will wait a long time.

**`model abstained` has two causes.** Either the checkpoint scored every candidate below the `0.8`
floor, or the request arrived with no token names at all. The game log cannot tell those apart; that
distinction only exists on the service side, in `/health` and in the probe.

The fallback path also writes a second, separate line first, at **Warning** severity while everything
else on this path is `Log`:

```
Warning: Intent keyword fallback: model service unavailable
Log:     Intent request finished (applied): Rules applied (keyword fallback: model service unavailable).
```

If you are grepping the log for failures, that Warning is the string to grep for — it is the only
line that appears when the box is down, and the loadout still applies, so nothing else looks wrong.

## Step 8 — When the log says X, the bug is in Y

The value is only worth having if it points somewhere. This mapping is the actual deliverable of
P31:

| what the log says | look here first |
|---|---|
| `exclusion` | `exclusion.py`: the marker detection and the complement set it built. Every bucket it emits is `default` by construction, so a numeric change attributed to this layer is a wiring bug, never a misreading |
| `clauses` | `clauses.py`: which path answered (`min_params=2` for a conjunction, `min_params=1` for a single clause), singular versus plural, and the parameter word it paired with the adjective |
| `model` | the checkpoint. Read the per-family table from `evaluate.py` first — if the family is healthy there, the corpus never taught that wording and the fix is in `generate_dataset.py`, not in the weights |
| `model`, but you expected a rule | the rule refused. Probe that exact phrase with `Saved/verify_intent_source.py` and `measure_clauses.py` before touching anything |
| `keyword fallback` | nothing in the model. `/health` first, then the port, then whether the process is running at all |
| absent | the service works but predates the header, or the header was stripped. Restart the service and re-post |

Every message the validator can print, and what each one means you did:

| `Rules rejected:` message | where | what it means |
|---|---|---|
| `The model response cannot be checked against the ability list.` | `:138` | no token table loaded, or the body exceeded 65536 characters |
| `The model returned an invalid rules response. No rules were changed.` | `:146` | not JSON, or the root is not exactly `{"tokens": [...]}` |
| `The model returned no usable ability selection. No rules were changed.` | `:150` | more entries than the table has tokens (an empty list is caught earlier, in the controller) |
| `The model returned an invalid ability entry.` | `:158` | an entry is not a JSON object |
| `Each model ability must contain only a name and parameter buckets.` | `:166` | an entry has a third key, or is missing `name` or `params` |
| `Unknown parameter or non-bucket value: Fireball.Damage.` | `:186` | the service named a parameter the token does not have, or sent a number instead of a bucket |
| `The ability table contains an invalid parameter range.` | `:192` | a range has a non-finite bound or `Min > Max` |
| `Unknown model bucket 'Z'. No rules were changed.` | `:202` | a label outside the five in `schema.BUCKETS` |
| `The ability parameter could not be safely resolved.` | `:207` | the clamped value came out non-finite |
| `No ability list is loaded, so the rules cannot be checked.` | `:34` | the token table is null game-side |
| `'X' is not an ability I know. Try: ...` | `:55` | the selected name is not in `DA_AbilityTokens` |
| `Only 1 Defense ability is allowed, but the rules asked for 2.` | `:77` | the category cap was broken |
| `'X' was listed more than once.` | `:89` | a duplicate was selected |

The first nine rows are the structural checks, and they are the reason the layer went into a header
rather than a key: a provenance field would have tripped the `:146` row on every successful request.
The last four are semantic -- the response was well-formed but asked for something the token table
does not allow.

## Step 9 — What a header buys, and what it costs

What it buys: attribution in the log, with zero risk to the payload, and no new field for the
validator to disbelieve.

What it costs, honestly:

- **An invisible contract.** A typo in the header name or in a source string fails *silently* back
to the generic line, because an absent header is a legal state. Nothing type-checks this.
- **A restart, not a reload.** Any edit to those four exits needs uvicorn restarted before the game
  can see it, and the C++ side needs Live Coding. Two steps for one line of output.
- **Developer-only.** The player still sees the generic `ClientMessage`. This is a tool for reading
the log, not UI, and it should not be sold as more than that.

## Step 10 — Where this leaves the model

Unchanged, and that is the point. `model.pt` is still P24 at MD5
`3eb0c428402a9691eddaf206f91985d2`; `clauses.py` and `exclusion.py` were not modified at all; the
corpus, both test-split hashes and `CURATED_INTENSITY` are untouched. Every number from PART 8.12
still means what it said.

What changed is that those numbers are now **checkable from inside the game**. A rule that fires on
198 rows is only useful if, when one of them looks wrong, you can see in the log that a rule — not
the model — answered. That is the difference between a measurement and a claim.

---

## Step 11 — Separate the rule's error from the label's impossibility

**The instrument was missing.** `exclusion.py` was the only shipped component with no miss list, so its
0.794 was a number with no rows attached to it. Adding `misses("exclusion", ...)` printed 27 rows, and
they all had the same shape: **the ability set was right and one bucket was wrong.**

That is a signal about the corpus, not about the rule. A rule that has never once got the set wrong and
misses only on buckets is either reading the wrong words, or reading words that are not there.

**Check what the label asserts that the sentence does not say.** Take one row literally:

```
without projectile, LightningStrike and shield
  got  Invisibility[...all default], SpeedBoost[...all default]
  want Invisibility[...all default], SpeedBoost[Cooldown=higher ...default]
```

Nothing in those six words means "higher cooldown". Before designing a fix, find out where the bucket
came from — that is a `Source` question and it has an answer:

```python
# generate_dataset.py -- the renderer prints the EXCLUDED names only
def fam_exclude_without(rng, s):
    excluded = [n for n in CURRENT_CANDIDATES if n not in s.tokens]
    return f"without {join_natural([s.words[n] for n in excluded])}"

# ...while prepare() has no branch for the family, so it falls through to this:
return sample_factory(rng, mentioned_count=rng.choice([0, 1]))
```

`mentioned_count=1` pins a random bucket on a **surviving** token; the renderer never prints it. The
label is real, the evidence is absent, and 21% of the family is affected — 29 of the 138 live rows.
(That is the exclusion families alone. The same fallthrough hits six more families and takes the live
 total to 66 rows — Step 13.)

**Test the tempting hypothesis before acting on it.** The trailing words look like intensities —
`haste`, `turbo`, `quick`, `shield` — so the fix that offers itself is "read the bucket off the last
word". One counter-example kills it: `without strike, guard and quick` is labelled
`Invisibility Duration=lower`, and no reading of `quick` produces that. `quick` is a SpeedBoost alias,
and the rule is already right to exclude SpeedBoost for it. These are ability names; the live catalogue
simply does not contain the abilities most of them name.

**Then state the ceiling, because it changes what the number means.**

- 76 of the 537 live rows carry a label their text does not determine
- so **0.858 intent exact is the most any system can score on this split**

**Then go back and check the ceiling itself, because the first version of this step got it wrong.** This
was originally written as 40 rows and a 0.926 ceiling, because `undetermined()` only looked at families
whose name starts with `exclude_`. The mechanism has nothing to do with that prefix — `prepare()` has no
branch for **ten** families and every one of them falls through the same way. Extending the check moved
the count from 40 to 76 and the ceiling from 0.926 to 0.858. A ceiling is a claim about the corpus and
deserves exactly the suspicion an accuracy figure gets: an under-counted ceiling makes the system look
worse than it is and aims the next hour of work at the wrong layer. Step 13 is that correction.

**Decide, out loud.** `exclusion.py` was not changed. A rule that guessed a bucket would be choosing
blind, with 104 correct rows exposed to regression and no evidence available to gain. The generator
defect is recorded and not repaired, because regenerating moves 66 test and 316 train labels and retires
every published figure. What changed is the *measurement*: each rule is now scored twice, and every miss
carries its reason.

| reading | exclusion rule | clause rule |
|---|---|---|
| raw | 104/131 (0.794) | 188/198 (0.949) |
| text determines the label | **104/104 (1.000)** | 185/192 (0.964) |
| misses that are corpus noise | 27 of 27 | 3 of 10 |

**The transferable lesson.** "A rule at 0.79 needs work" and "a rule at 1.00 over a corpus that is 21%
self-contradictory" are the same number and opposite instructions. Print the misses, then ask whether
the miss was ever *in* the sentence, before touching the rule.

---

## Step 12 — Measure the composition the game runs, not the code you wrote

Steps 10 and 11 both scored `exclusion -> clauses -> model`, because that is what `serve.py` does. That
number was published for several sessions before anyone noticed it was not the game. **A player types
`how about attack` and gets Fireball. The measured path recorded that row as nothing and counted it
wrong.**

The measurement was not buggy. It was measuring the wrong object. This step is about closing that gap,
and it is worth doing even when — especially when — your numbers look stable.

#### 1. Count the layers by reading the client, not the service

Do not assume the HTTP service is the last word. Search the client for what it does when the service
gives it an answer it cannot use:

```bat
grep -n "ApplyKeywordFallback(" Source/WishCraft/WishCraftPlayerController.cpp
```

Three call sites, and one of them is the one that matters:

| trigger | reason string |
|---|---|
| service unreachable or non-200 | `model service unavailable` |
| exactly one root field `tokens`, array **empty** | `model abstained` |
| the request could not be started | `model request could not start` |

That middle row is the whole finding. **An empty intent is a signal, not a failure.** The client reads
`{"tokens": []}` as "the model declined" and answers the phrase itself with
`URuleParserLibrary::ParseRuleText`. So the real composition is four layers, and the fourth one is in
C++ where no Python measurement will ever see it by accident:

```text
apply_exclusion  ->  apply_clauses  ->  model  ->  (client) keyword parser
```

`serve.py`'s own docstring had been saying this the entire time — "If nothing clears the confidence
floor the response is an empty list, which is the signal to fall back to the keyword parser." The
docstring was right and the measurement had never been taught it.

#### 2. Split the misses before you count them

The composed path had no miss list at all; only the two rules did, and that asymmetry is why this went
unnoticed. `diagnose_shipped()` in `measure_clauses.py` prints each wrong row tagged by what is
actually wrong:

* `SET` — the wrong abilities were named. Prints `missing [...]` and `spurious [...]`.
* `BKT` — the right abilities, a level misread. Prints `truth -> predicted` per parameter.

That split changed the work completely:

| failure | rows | what it means |
|---|---|---|
| wrong token set | 64 | presence — the path named the wrong abilities |
| right set, wrong bucket | 67 | direction — the abilities were found, a level was misread |

The `missing` / `spurious` counts then said something a single accuracy number cannot: across all 64
set errors there was **one invented ability**. The path was not guessing wrong — it was saying
*nothing*. 49 of the 64 were `request_can_we` (24), `allow_terse` (18) and `only_prefix` (7), the three
families `test.jsonl` holds out by whole family, so the model had never seen their frames.

**Read that as the diagnosis it is.** "The model is 0.756" says fix the model. "The model names nothing
on frames it never saw, and the client already has a parser for exactly those" says look at the
composition. Only one of those is a change worth making.

#### 3. Measure the fourth layer without changing any code

The fallback is deterministic, so it does not need to be guessed at. A client fallback can only fire
where the service returned an **empty list**, which means it cannot unseat a single row any earlier
layer answered. That makes the measurement exact rather than an estimate — you only need one question
answered: of the declined rows, how many does the parser get exactly right, and does it break any
row that was right because its truth was empty too?

`apply_client_fallback()` does the substitution with `evaluate.baseline_intent`, and `fallback_gain()`
reports the accounting:

```text
keyword parser as last resort (only on rows the path declined)
  declined rows            : 40
  parser exactly right     : 8   (would become correct)
  parser would break       : 0   (declined rows already correct)
  intent exact now         : 0.756
  intent exact projected   : 0.771  (+0.015)
```

#### 4. Verify the port before you trust the number

`evaluate.baseline_intent` claims to be a faithful port of `ParseRuleText`. If that claim is wrong, the
"as played" figure is fiction. Four things to check, all in `Source/WishCraft/Rules/RuleParserLibrary.cpp`:

1. **Normalisation** — `Normalize()` lowercases, then strips space, hyphen and underscore. The port must
   strip the same three and no others.
2. **Match order** — the token's own name is tried by substring first, then each keyword. A keyword match
   must not require the name to match.
3. **Which table** — `ParseRuleText` iterates `Table->Tokens`. That is the **live 3-token** set, the same
   one as `catalogue_live.json`, not the six-row `catalogue.json`.
4. **Ordering** — the C++ `AddUnique`s then sorts with `FNameLexicalLess()`; the port appends in
   catalogue order. This one does not matter, and saying why is the point: every comparison in the
   scoring is set-based (`keyed()` / `as_label()`), so token order cannot affect a score.

#### 5. The number, and what it means

| path | token set | intent exact | bucket |
|---|---|---|---|
| keyword baseline | 0.339 | 0.128 | 0.797 |
| model alone | 0.594 | 0.391 | 0.888 |
| service (exclusion + clauses + model) | 0.881 | 0.756 | 0.950 |
| **as played (service, then client keyword fallback)** | **0.909** | **0.771** | **0.950** |

The service figure is the one a change to `Tools/intent_model` can move. The as-played figure is the
one a player experiences. When the two differ, report both — a headline that quietly means "the
service" while a reader assumes "the game" is the same class of error as a rule scored against a corpus
that contradicts itself. The number was not wrong. It was answering a question nobody had asked.

#### 6. The trap: do not add the fallback to the service

The obvious response — put the keyword parser on the server as a fourth layer — is wrong twice over.

* It **duplicates** a layer the client already runs, so nothing improves.
* It **consumes the signal**. The client would stop logging `model abstained`, and that log line is the
  only evidence that the confidence floor is what silenced a phrase. You would trade a diagnostic for
  nothing.

**The transferable lesson.** Before optimising a number, confirm the number describes the thing the
player touches. Read the composition off the client, not off the service, and count a layer you did not
write. A 0.015 error in what you thought you were measuring is more expensive than a 0.015 gain, because
every decision after it is aimed at the wrong component.

---

## Step 13 — Audit the ceiling itself, because a noise count is a claim

Step 11 ended with a ceiling: "40 of the 537 rows carry a label their text does not determine, so 0.926
is the most any system can score." That ceiling was an **under-count**, and the miss list was already
showing it. These rows were sitting in the output, charged to the path:

```text
BKT  only cloaked
     (only_prefix)  Invisibility.Cooldown much_lower -> default
BKT  faster
     (allow_terse)  SpeedBoost.Multiplier default -> higher
```

`only cloaked` names one ability and says nothing about cooldown. `faster` is *labelled all-default* and
the model answered `higher` — the corpus is wrong and the model is right. Neither row was counted as
impossible, so both were counted as the path's fault.

#### 1. The cause is a prefix, not a fact

`undetermined()` asked:

```python
if row.get("family", "").startswith("exclude_"):
```

That is a claim about family *names*. The defect is about which families `prepare()` has no branch for.
Two different things, and only one of them is true.

#### 2. Establish the real set from source, not from the rows

Eyeballing rows tells you a row looks impossible. It does not tell you *which* rows are impossible by
construction, and you need the second to count fairly. Read `generate_dataset.py` in this order:

1. **`prepare()` (line 1028 onward).** Walk it top to bottom and write down every family with a branch.
   It handles `ABSTAIN_FAMILIES`, `category_only`/`category_verb`, `only_suffix`, `param_single_max`,
   `param_single*`, `param_multi_conflict`, `param_multi_agree`, `param_multi_together`,
   `param_speed_contrast`, `equip_only`. Anything else reaches the last line:

   ```python
   return sample_factory(rng, mentioned_count=rng.choice([0, 1]))
   ```

2. **`sample()` (line 512).** This is what `mentioned_count` *means*, and you cannot guess it.

   ```python
   for _ in range(mentioned_count):
       name = rng.choice(names)
       params = list(param_ranges(CURRENT_TOKENS[name]))
       mentioned.setdefault(name, [])
       candidate = rng.choice(params)
       if candidate not in mentioned[name]:
           mentioned[name].append(candidate)
   ```

   One random `(token, parameter)` pair is marked "driven". `sample_buckets()` then gives every driven
   parameter a random bucket from `BUCKETS`, weighted, and leaves the rest at `default`.

3. **Each renderer.** Confirm none of them can print an intensity word. They are built from `s.words[n]`
   (the ability aliases) plus fixed frame text:

   | family | rendered from | intensity word? |
   |---|---|---|
   | `allow_conversational` | `words[n]` + `" only"` | no |
   | `allow_terse` | `words[n]` joined by `", "` or `" "` | no |
   | `only_prefix` | `only {names()}` | no |
   | `only_verb` | `just give me {names()}` | no |
   | `exclude_*` (four families) | the **excluded** `words[n]` | no |
   | `request_want` / `request_can_we` | `{names()}` | no |

   Ten families, one mechanism: the label can assert a bucket and the sentence has no way to say it.

#### 3. The trap: never encode this as a prefix or a substring test

`only_suffix` **is** handled — `prepare()` narrows it to `mentioned_count=0` at line 1059 and rewrites
every bucket to `default`, so its labels are honest. `only_prefix` and `only_verb` are not handled.

A test on `startswith("only")` would have swept in 60 correct `only_suffix` rows and declared them
impossible. That is the same error one level up: a naming heuristic standing in for the mechanism. The
fix is a **literal frozenset of the ten family names** (`UNEXPRESSED_BUCKET_FAMILIES` in
`measure_clauses.py`) with a comment recording the line numbers it was read from, so that when
`generate_dataset.py` gains a branch for one of them the list visibly has to shrink.

*(Superseded in P38 — the list became `BUCKETLESS_FAMILIES` in `dataset_audit.py`, and
`measure_clauses.py` now imports it as `UNEXPRESSED_BUCKET_FAMILIES` so the two cannot drift. See
PART 8.14.)*

#### 4. Check that one mechanism predicts one rate

If the ten families really share a mechanism, the affected fraction should fall out of
`BUCKET_WEIGHTS` rather than being observed and accepted:

```text
P(mentioned_count = 1)         = 0.5
P(bucket != default)           = 1 - 0.55   (BUCKET_WEIGHTS[DEFAULT_BUCKET] == 0.55)
P(unexpressed bucket in a row) = 0.5 * 0.45 = 0.225
```

The three measurements:

| measurement | value |
|---|---|
| predicted from `BUCKET_WEIGHTS` | 0.225 |
| measured, all 323 live rows in fallthrough families | 66/323 = **0.204** |
| measured earlier, exclusion rows only | 29/138 = **0.210** |

Three numbers clustering on one arithmetic is the evidence that this is a single mechanism and not a
family-specific quirk. If the new families had shown 5% or 60%, the list would have been wrong.

#### 5. The corrected numbers

| | before | after |
|---|---|---|
| undetermined rows | 40 | **76** (`66 label not in text`, `10 same words, two labels`) |
| ceiling on the split | 0.926 | **0.858** |
| composed path, determinate rows | not measured | **399/461 (0.866)** service, **407/461 (0.883)** as played |

Reproduce both readings with one command:

```bat
Saved\run_measure.bat
type Saved\measure_clauses.log
```

The header prints the undetermined count broken down by reason, and the tail prints
`determinate_reading()` for the service path and the as-played path.

**What this changes about the headline.** 0.771 used to read as "91 rows short of 0.926". It now reads
as **54 wrong out of 461 answerable rows**. Those are different projects: the first invites you to
chase a quarter of the split that no implementation can reach, the second is a bounded, real target.
And the exclusion rule and the clause rule both held exactly where they were — `104/104` and
`185/192` on determinate rows — which is the check that the extension moved *labels*, not judgements.

**The transferable lesson.** An accuracy figure and a noise figure are the same kind of claim and need
the same scrutiny. "40 rows are impossible" is not an observation, it is a rule you wrote in code; read
the generator to find out which rows the mechanism actually covers, then check that one mechanism
predicts one rate. A ceiling set too low quietly retires work that was worth doing.

---

# PART 8.14 — Audit the answer key before you audit the model (P38)

This part exists because of a question a player would ask and a researcher would not: *can I ship this?*
By PART 8.13 the headline was 0.771 with a ceiling of 0.858, and the build was comfortable calling the
remaining 0.087 a corpus problem. Comfortable is not the same as correct. If roughly one row in six has a
label its own sentence cannot express, then **the ceiling is the thing to fix first**, because no model,
no rule and no amount of architecture can score that row. And a model trained against contradictory labels
learns to hedge -- which is exactly what a player would find when they typed the phrase it hedged on.

So this part is not about the model at all. It is about the generator, and about building a check that
refuses to let the generator lie to you again.

## Step 1 — Write the audit as a module, not a script

`Tools/intent_model/dataset_audit.py` does two things and refuses to do a third: it finds the two defect
classes a sentence cannot express, and it counts them. It is **stdlib only** -- no torch, no numpy -- on
purpose, because `generate_dataset.py` imports it.

The API, in the order you will use it:

| name | what it is for |
|---|---|
| `BUCKETLESS_FAMILIES` | the ten family names whose renderers cannot print intensity |
| `normalise_text` | one canonical string per sentence, so two spellings collide honestly |
| `label_key` | one canonical string per label, so two labels compare honestly |
| `load_splits` | reads the four `.jsonl` files into `{split: [rows]}` |
| `moved_params` | which parameters a label actually moves |
| `unexpressed_bucket_problems` | defect class 1 |
| `ambiguous_texts` / `ambiguity_problems` | defect class 2 |
| `find_problems` | both classes, over a loaded corpus |
| `undetermined_count` | the per-split rate, for the header `measure_clauses.py` prints |
| `census` | rows per family, with the bucketless ones marked |
| `main()` | standalone run: prints the report, exits non-zero on a failure |

Run it standalone against whatever is on disk:

```bat
.venv\Scripts\python.exe Tools\intent_model\dataset_audit.py
```

Redirect it when you want a record you can quote later -- this is how the baseline below was captured:

```bat
.venv\Scripts\python.exe Tools\intent_model\dataset_audit.py > Saved\dataset_audit_v1.log 2>&1
```

## Step 2 — Defect class 1: a family that cannot express its own label

`prepare()` branches on family and hands the row a `mentioned_count`. Ten families have no branch and fall
through to the last line of the function:

```python
return sample_factory(rng, mentioned_count=rng.choice([0, 1]))
```

`mentioned_count=1` means "mark one `(token, parameter)` pair as driven", and driven parameters get a
weighted random bucket (`default` at 0.55). Meanwhile every one of those ten families renders its sentence
from `s.words[n]` plus fixed frame text -- `only the {ability}`, `give me the {ability}`, `no {ability}`.
There is no word in the sentence that could justify `much_lower`.

`BUCKETLESS_FAMILIES` is the fix, and it is a **literal list of ten names**, not a prefix test:

```python
if family_name in BUCKETLESS_FAMILIES:
    return sample_factory(rng, mentioned_count=0)

return sample_factory(rng, mentioned_count=rng.choice([0, 1]))
```

Two things about that list matter more than the code.

**It is literal because the defect is structural, not a naming convention.** P37 had already shipped the
wrong version of this check once -- `row["family"].startswith("exclude_")` -- which found the `exclude_*`
rows and silently missed every other fallthrough family. A prefix test is a claim about names; membership
is a claim about the mechanism.

**It must shrink, and it is written so you can see that.** `only_suffix` is deliberately *absent*: it looks
like a bucketless family, but `prepare():1059` narrows it to `mentioned_count=0` and rewrites every bucket
to `default`, so its labels are honest and a prefix test would have declared 60 correct rows impossible.
The constant carries the line numbers it was read from. Add a branch to `prepare()` and the list has to
shrink or your audit starts failing rows that became legal.

## Step 3 — Defect class 2: one sentence, two labels

A supervised model learns `f(sentence) -> label`. Two rows that normalise to the same sentence and carry
different labels set a hard floor under the loss. `ambiguous_texts` groups `normalise_text(row)` over
`train` + `valid` + `test` and keeps groups with more than one `label_key`. `test_heldout` is excluded
deliberately, mirroring `measure_clauses.build_ambiguity`: it is a detection probe, not an accuracy split,
and the same sentence is *supposed* to appear there.

Corpus v1, before any fix, contained both classes in quantity:

| | count |
|---|---|
| `label not in text` | 918 |
| `same words, two labels` | 57 |
| undetermined, train / valid / test / heldout | 660/7,823 · 84/600 · 194/1,166 · 58/289 |

## Step 4 — Gate the write, or you will do this again

An audit that only prints is a report, and the next regeneration reintroduces the defect while the report
scrolls past. So `main()` calls `find_problems(corpus)` and exits non-zero, and **all four `write_jsonl`
calls sit after it**. A failing corpus writes nothing at all.

This is not theoretical. The first regeneration attempt was stopped by the gate on three
`param_multi_together` collisions and left the previous corpus on disk untouched. That is the check paying
for itself on its first use, and it is the single most important design decision in this part.

## Step 5 — Fix the generator, three edits

**Edit 1: the fallthrough.** Step 2 above. This is a *label* fix, not a renderer fix: the sentence already
means "all default", so the label should say so. Inventing intensity wording inside those renderers would
be a new family, not a repair.

**Edit 2: `much_lower` gets its own frames.** `_param_single_sentence`'s else-branch served `lower` and
`much_lower` with identical wording, in both halves of the function (`:659` curated, `:668` train). Now
`much_lower` has `make the {p} on {word} much lower`, `much less {p} on {word}` and
`lower the {p} on {word} a great deal`, and `lower` keeps the originals. This is the one defect a
label-only repair cannot fix -- the sentence genuinely has to change.

**Edit 3: `fam_param_multi_together`'s frames become bucket-aware.** The family emitted identical frames
for `higher`/`higher` and `max`/`max`. It now only offers the `both ...` wording when `bucket_a == bucket_b`.

After the three edits, regenerate and audit:

```bat
cd Tools\intent_model
..\..\.venv\Scripts\python.exe -u generate_dataset.py --seed 20261108
```

The generator prints the audit result inline and refuses to write on failure. What a passing regen looks
like:

```text
audit problems : 0
train/test text overlap   : 0
wrote 9882 rows
```

## Step 6 — The defect the audit cannot see

The audit compares the corpus with itself. It says nothing about the code that reads the corpus, and the
first v2 measurement came back with 12 rows of `make the {p} on {word} much lower` answered as `lower`.
The cause was in `clauses.py`: the lexicon is single-word, so a two-word intensifier resolved on its last
word. The fix is a table checked *before* the lexicon:

```python
GENERIC_MULTIWORD = [
    (("much", "lower"), "much_lower"),
    (("much", "less"), "much_lower"),
    (("a", "great", "deal"), "much_lower"),
]
```

`param_single_superlative` goes from 12 wrong to **101/101** on the clause rule. Write this down as a
lesson rather than a bug: *auditing the corpus is necessary and not sufficient.* Every measurement is a
pair, corpus and reader, and a fix to one can expose the other.

## Step 7 — Retrain, restart, re-baseline (in that order)

`train.py` at its defaults (30 epochs, patience 100), P39 lands at best epoch 16 of 30. Then **restart the
service**: uvicorn runs with no `--reload`, so a new `model.pt`, or a new `clauses.py`, is invisible to a
running server. Confirm with `/health` before measuring anything — it now also reports the confidence floor
it loaded, which is the thing PART 8.15 changes.

```bat
Saved\run_train.bat
Saved\run_measure.bat
type Saved\measure_clauses.log
```

The header is the whole point of this part:

```text
live candidates : Fireball, Invisibility, SpeedBoost
test rows       : 588 of 1166 kept
undetermined    : 0 rows whose text does not determine its label
ceiling         : 1.000 intent exact is the most this split can score, by any system
overlap         : 0 rows fired by both rules (should be 0)
```

And the four paths, on the same 588 rows:

| path | token set | intent exact | bucket | bkt\|tok |
|---|---|---|---|---|
| keyword baseline | 0.355 | 0.160 | 0.837 | 0.750 |
| model alone (P39) | 0.660 | 0.415 | 0.898 | 0.855 |
| service | 0.929 | **0.912** | 0.994 | 0.993 |
| as played | **0.939** | **0.922** | 0.994 | 0.993 |

Corpus v1 read 0.909 / 0.771 / 0.950 as played and 0.881 / 0.756 / 0.950 through the service, against a
ceiling of 0.858. **Do not put those two columns in the same table as a delta.** Corpus v2 changed the
answer key, the wording, the checkpoint and all four measurements at once; nothing in the difference
isolates one cause, and quoting it as an improvement would be the same mistake P37 spent a whole phase
unpicking. Corpus v3 (PART 8.15) is the same story again: it changed six lines of `pick_words`, which
changes which alias each name is drawn from, so every row is re-rendered even where the counts match.

The heads that survived the rebuild now clear their own constants on the full six-token `test.jsonl`:
`moved` 0.941 against the 0.889 always-`default` constant, `direction` 0.709 against 0.469. Held-out
`Blink` detection is 0.072 at 0.087 false-positive — worsened by the 0.50 floor and still bad; PART 8.15
says why that number is the project's biggest honest gap.

**What is still broken, and how to say it out loud.** 42 of the 588 live rows name the wrong token set and
10 name the right set with the wrong bucket — 52 misses, 0.922 as played. Almost every wrong set is a name
**missing**, never one invented: the model under-detects. Splits, and the one artefact worth knowing:

- **24 rows** sit in `request_can_we` (13) and `allow_terse` (11). `ball of fire, ghost` loses Invisibility;
  `can we do fireball` loses Fireball. Terse and question-shaped phrase, name present, answer short.
- **10 rows** across `exclude_without` (6) and `exclude_all_but` (4) are **not model errors**. Their
  sentences name `LightningStrike` / `Shield` — tokens the catalogue marks `planned` and the game does not
  ship — so the complement over three live tokens is "all three" and nothing can resolve `lightning`.
  `measure_clauses.py` drops rows whose *label* names an unshippable token, but not rows whose *sentence*
  does. Read those ten as a scope mismatch, not a miss.
- **5 rows** in `only_prefix`, **2** in `param_single_superlative`, **1** in `category_verb` (which the
  client parser then fixes).
- **`clauses.py` still fires once and misses on `allow_terse`** (`invisibility quicker`).

Zero undetermined rows does not mean zero defects. It means every remaining defect is now *decidable* --
each one is a real judgement the path got wrong, not a label no path could have reached. That is the
difference between a corpus you can ship against and one you can only report on.

## Verified in game

Five phrases typed into `WBP_RuleInput` and sent with the service running, each checked against the
loadout the GameState mirrors *and* the game's own log:

| phrase | applied |
|---|---|
| `give me the fireball` | Fireball only: Damage 20 / ProjectileSpeed 2500 / Cooldown 1.5 |
| `only the invisibility` | Invisibility only: Duration 5 / Cooldown 8 |
| `give me everything except the fireball` | Invisibility + SpeedBoost, Fireball absent |
| `make the damage on the fireball much lower` | Damage **`much_lower` -> 5.000** (range minimum), other two unchanged |
| `make me a pizza` | `model abstained`, nothing matched, loadout unchanged |

The fourth row is this whole part proved end to end -- the phrase the corpus could not express before.

**One thing to know about abstain.** The controller does not stop at an empty token list: it logs
`Intent keyword fallback: model abstained` and *then* runs `URuleParserLibrary::ParseRuleText`. So an
abstained phrase that happens to contain an ability keyword will still apply that keyword's match. None
of the five above does -- `make me a pizza` matched nothing -- but if a phrase moves a loadout you did not
expect, check whether it was the model or the fallback before blaming the model.

## Pitfalls, in the order they will bite you

1. **A printed audit is not a gate.** If the write is not downstream of the check, the next regen undoes it.
2. **Never name a defect by family prefix.** `startswith("exclude_")` found 76 rows and missed the rest of
   the fallthrough; membership in a literal set is the honest form.
3. **Do not repair a label without asking whether the sentence can express it.** Two choices, never both
   in one pass: state the label the sentence already means, or change the sentence. Doing one for some
   rows and the other for the rest is how a corpus becomes unmeasurable.
4. **Keep the `measure_clauses.py` import as a canary.** It imports `BUCKETLESS_FAMILIES` from
   `dataset_audit` rather than keeping a second copy, so the two cannot drift.
5. **Restart uvicorn after any change to `model.pt`, `clauses.py` or `exclusion.py`.** There is no
   `--reload`; a stale process will happily serve the old answer all day.
6. **Never compare numbers across corpora**, and never judge on `valid.jsonl` -- it holds two families and
   rewards a model that says "all default".

---

# PART 8.15 — A defect in the vocabulary, and a constant nobody had swept (P39, P40)

PART 8.14 ended in a good place and an easy trap. The corpus had **0** undetermined rows and a live ceiling of
`1.000`, so every remaining miss was now a genuine judgement call -- and the tempting next move is to read
"genuine judgement" as "the model needs more capacity" and spend a week on architecture. Instead this part is
two cheap experiments, both counting exercises rather than training runs:

1. one **word in the vocabulary was doing two jobs**, and
2. one **threshold in `serve.py` had a comment defending it and no measurement behind it**.

Together they are worth 0.879 -> **0.922** as played. Neither touches the model's shape.

## Step 1 — Count the word before you blame the model

PART 8.14 closed with 62 of its 588 live rows naming the wrong token set, and its own residual list named the
cause in one line without fixing it: *most are one collision, `faster` / `quicker` / `fast` present against
`SpeedBoost` absent*. A symptom made it concrete in the game's own words:

```text
make the projectile speed on ball of fire much lower   ->   Fireball  AND  SpeedBoost
```

The way to localise that is not to read predictions. It is to count rows. On the train split:

| count | value |
|---|---|
| training rows | 7,788 |
| rows containing a speed word | **1,495** |
| of those, labelled SpeedBoost | 820 |
| of those, SpeedBoost absent | 675 |
| rows using `faster` with SpeedBoost | 122 |
| rows using `faster` without SpeedBoost | 164 |

`faster` is right 43% of the time. That is not a model being unsure -- that is a word carrying no signal, because
it means two different things in two different sentence shapes:

| sentence | what the word is | what the label says |
|---|---|---|
| `make the fireball faster` | a **ProjectileSpeed adjective** | Fireball, ProjectileSpeed `higher` |
| `sneak, faster` | **SpeedBoost's name** | SpeedBoost equipped, everything `default` |

Same spelling, two meanings, split roughly evenly. No amount of training separates them, because a trigram
encoder sees only the word.

**Where the second meaning came from** is the part worth internalising. `catalogue.json` lists `faster`,
`quicker` and `quick` among SpeedBoost's keywords -- correctly, they are real names for it -- and
`build_alias_maps` promotes any keyword not shared with another token into a surface **name**. `pick_words` then
drew each ability's name from that pool, so **60 training rows named SpeedBoost `faster`**.

The check to run any time a family scores badly on *presence*: for each suspect word, count the rows it appears
in **split by whether the token is in the label**. Two near-equal columns mean the word cannot carry the
decision. (PART 8.10's support count answers a different question -- whether a family has training rows at all.)

## Step 2 — Filter the slot, not the word

The fix is six lines in `pick_words` (`generate_dataset.py:504`), and the shape of it is the whole lesson:

```python
return {
    name: rng.choice(
        [w for w in CURRENT_ALIASES[name] if w.lower() not in SPEED_ADJECTIVE_WORDS]
        or CURRENT_ALIASES[name]
    )
    for name in CURRENT_CANDIDATES
}
```

with

```python
SPEED_ADJECTIVE_WORDS = {"fast", "faster", "quick", "quicker", "rapid", "slow", "slower"}
```

Three things to notice, because each one is a decision.

**It removes the word from the *name slot* only.** The adjective survives everywhere it is legitimate, which is
exactly where `fam_param_speed_contrast` does its work: `make the fireball fast` must still teach
ProjectileSpeed `higher`, and `make the speed boost blazing fast` must still teach Multiplier `higher`. Deleting
the word would have removed a real, correct request from the vocabulary.

**The `or CURRENT_ALIASES[name]` fallback is not defensive padding.** An ability whose every alias is a speed
word would otherwise become unnameable, and an unnameable candidate is a presence row with no answer -- a worse
corpus than the one being repaired. The fallback makes the filter a preference rather than a restriction.

**`_equip_word` had already filtered this exact set since P23.** Only `pick_words` was left. When one function
filters a word class and its sibling does not, that asymmetry is usually the bug: the second function is the one
nobody remembered.

Two fixes were rejected. Deleting the words from SpeedBoost's keywords in `catalogue.json` removes a real
request (`give me faster`). Making `build_alias_maps` refuse adjectives outright rewrites `test.jsonl`, because
`pick_words` feeds every family, which would void every number in this document. The narrow filter is the only
change that fixes the collision without moving the answer key.

## Step 3 — Regenerate, then accept that you cannot diff it

```bat
Saved\run_regen.bat
type Saved\regen_v3.log
```

```text
audit problems : 0
train/test text overlap   : 0
wrote 9882 rows
```

The split is unchanged -- 7,823 / 600 / 1,166 / 293 -- and the two families that were already surface-exhausted
still are (`category_only` wanted 193, got 14; `category_verb` wanted 47, got 12). Those are a pre-existing limit
of the renderers, not a result of this change.

**Corpus v3 is not diffable against v2, and this is the moment to stop trying.** `pick_words` decides which alias
each name is drawn from, and names appear in almost every family's frames, so changing the filter re-renders every
row -- including the rows whose counts match v2 exactly. A row-level diff shows thousands of changes and tells you
nothing. That is why every table in this document is read **within a column**: P38 and P39 are two corpora, and
the delta between them contains the wording change as well as the fix.

Then retrain at the defaults and restart the service:

```bat
Saved\run_train.bat
type Saved\train.log
```

P39 lands at **best epoch 16 of 30**, valid `exact` 0.840 against P38's 0.812, **1,478,919** parameters (one more
vocabulary word), MD5 `8866045eb4ad4014523a7437d6d51b6a`.

## Step 4 — Read the family that proves the mechanism

| live 3-token (588 rows) | P38 / floor 0.80 | P39 / floor 0.80 | P39 / floor 0.50 |
|---|---|---|---|
| model alone, token set | 0.608 | 0.643 | 0.660 |
| service, token set | 0.894 | 0.918 | 0.929 |
| service, intent exact | 0.879 | **0.901** | **0.912** |
| as played, intent exact | 0.889 | **0.913** | **0.922** |
| wrong token sets | 62 | 48 | 42 |
| `param_single_superlative` wrong sets | 9 | **1** | 2 |

`param_single_superlative` is the family where the adjective and the name collided, and it goes **9 -> 1**. That is
the fix confirming itself on the exact rows the diagnosis predicted. `allow_terse` fell 22 -> 13 for the same
reason: its terse renders (`sneak, faster`) are where a speed word used to arrive as a second ability.

Two families moved the *wrong* way -- `exclude_without` 3 -> 6 and `exclude_all_but` 1 -> 4 -- and Step 7 is about
why that is not a regression. The last column's `2` is the new floor doing exactly what Step 6 says it does: the
looser floor admits one spurious SpeedBoost on an adjective the corpus is now clean about.

## Step 5 — Sweep a constant before you defend it

With the corpus clean, the last free knob was the presence floor, and it had never been tested. It was `0.8` from
the first checkpoint onward, and `evaluate.py` carried a comment explaining it *by pointing at `serve.py`* -- which
pointed back. That is a habit, not a decision, and it reads exactly like one.

`Saved/sweep_floor.py` is the cheap experiment: one forward pass, re-decoded at each floor, plus **200 abstain rows
from `train.jsonl`** as a false-positive control -- the only source of "the right answer is nothing".

| floor | token set | intent | invented tokens | declined rows | control FP |
|---|---|---|---|---|---|
| 0.80 | 0.643 | 0.401 | 157 | 66 | 0.00 |
| 0.70 | 0.646 | 0.405 | 164 | 59 | 0.00 |
| 0.60 | 0.650 | 0.410 | 170 | 55 | 0.00 |
| **0.50** | **0.660** | **0.415** | 173 | 51 | 0.00 |
| 0.40 | 0.662 | 0.417 | 176 | 49 | 0.00 |
| 0.30 | 0.663 | 0.418 | 179 | 48 | 0.00 |

Read it one direction at a time:

- **0.80 -> 0.50 is flat-to-better**, and costs +16 invented tokens for 15 fewer declines. 0.50 buys +0.017
token-set / +0.014 intent on model-alone, which becomes **+0.011 token set / +0.011 intent on the service and
+0.009 as played** once the rules have taken their rows.
- **0.50 -> 0.30 stops paying**: +0.003 more, for six more invented tokens and three fewer declines. That is the
knee, and it is why 0.50 ships rather than 0.30.
- **The control column is the interesting one.** False positives on the abstain rows are `0.00` at *every* floor,
including 0.30. The model's nonsense probabilities sit far below 0.3, so the floor was never doing the
abstention work -- the training objective was; the corpus carries explicit abstain rows for it. A floor is a
**precision guard for the deployed candidate set**, not the thing that teaches the model to say nothing.

Adopting the number means moving it **everywhere at once**: `serve.py:64` (`INTENT_CONFIDENCE`, default `"0.50"`),
`measure_clauses.py:43`, `evaluate.py:441`, `eval_live.py:37`. A report measured at a floor nothing runs at is
worse than no report, because it is a number someone will quote. `evaluate.py`'s comment now says why it matches
production instead of arguing for a value.

## Step 6 — Say what the new floor bought, and what it cost

**On the six-token stress set it is a wash, and that half has to be written down.** Token set 0.611 -> 0.607,
intent 0.407 -> 0.408. Held-out `Blink` detection rises 0.041 -> 0.072 and control false positives rise with it,
0.063 -> 0.087. The looser floor buys detection of the unseen token and pays for it in false positives -- correct
only because the six-token set is not what ships.

Hence the rule this part exists to establish: **quote a confidence floor together with the candidate set it was
measured on, or the number means nothing.** "0.50 is better" is false as a general statement and true for the
deployed three-token set.

**One number got worse.** `golden.jsonl` direction 0.867 -> 0.667 -- 2 of 15 moved labels. Golden is 20
hand-written phrases never used for selection, so two labels is not a signal; it is recorded here so the next
session does not rediscover it and read it as a regression. Writing down a small negative is cheaper than
defending a table.

## Step 7 — A reported miss is not always a model miss

The residual is `Saved/measure_clauses.log`'s own list: 42 wrong token sets and 10 wrong buckets over 588 rows.

| family | rows | what is wrong |
|---|---|---|
| `request_can_we` | 13 | name present in the sentence, absent from the answer |
| `allow_terse` | 11 | same: `ball of fire, ghost` loses Invisibility |
| `exclude_without` | 6 | **artefact** |
| `only_prefix` | 5 | `only attack, ghost and speed` loses two names |
| `exclude_all_but` | 4 | **artefact** |
| `param_single_superlative` | 2 | spurious SpeedBoost on an adjective now clean in training |
| `category_verb` | 1 | the client parser fixes this one |

Almost every genuine miss is a name **missing**, never one invented -- the model under-detects. But those ten
`exclude_*` rows are not misses at all, and seeing why is the point:

```text
without lightning and barrier
all abilities but the shock and barrier
```

The **generator's candidate pool includes the two `planned` tokens**, `LightningStrike` and `Shield`, so an
exclusion family can render a sentence excluding abilities the game cannot equip. The label is then correctly
"all three live ones" -- but no three-token system can resolve `lightning`, so `apply_exclusion` returns `None`,
the model abstains, and an empty set is scored as three misses. `measure_clauses.py` drops rows whose **label**
names an unshippable token (588 of 1,166 kept) but not rows whose **sentence** does.

The repair is a scope decision, and neither branch is taken here:

- restrict the generator's candidates to live tokens -- which collapses the `category_*` families, since only one
live token exists per category, so the corpus loses families to fix ten rows; or
- drop sentence-mentioning rows from the live measurement, the way their labels are already dropped -- honest, and
it makes the measurement describe the game that will exist once `LightningStrike` ships.

Until one is chosen, read those ten as a scope artefact and do not go near `exclusion.py` to "fix" them.

## Step 8 — The gap neither lever closed: `Blink`

Held-out token detection has now been measured across every architecture in this document, and the current number
is **0.072 detection at 0.087 false positives** -- about one in fourteen. The floor change accounts for +0.031
detection and +0.024 false positives, and that is all of it.

So the honest statement of vocabulary independence is **structural only**: nothing in `serve.py` or `predict`
refers to an ability by name, and a seventh ability really is a data change rather than a redeploy -- but if you
added one today, whether the model detected it would be luck. **Do not quote it as a measured capability.** This
is the largest honest gap in the model, and it is a corpus/objective problem (nothing in training supervises
"presence of a token never seen"), not a threshold one -- so PART 8.14's discipline applies: fix the thing the
measurement points at, and do not sweep a knob to make a headline move.

## What PART 8.15 leaves you with

| | value |
|---|---|
| checkpoint | P39, MD5 `8866045eb4ad4014523a7437d6d51b6a`, epoch 16, 1,478,919 params |
| shipped floor | **0.50**, in all four files |
| as played, live 3-token | token set 0.939 / intent **0.922** / bucket 0.994 |
| service | token set 0.929 / intent **0.912** |
| rules | exclusion 143/143 (1.000) on **corpus-shaped** rows only, clauses 210/210 (1.000) -- the exclusion number is not completeness; see PART 8.16 |
| live ceiling | 1.000, **0** undetermined rows |
| biggest gap | held-out detection 0.072 at 0.087 false positives |

## Pitfalls, in the order they will bite you

1. **A defect in the vocabulary presents exactly like a defect in the model.** 62 wrong rows, no bias visible in
   any single prediction, and the natural response is to train longer or widen the network. Count the word in
   each of its two senses first -- minutes of counting, six lines of fix.
2. **Corpus v3 is not diffable against v2.** `pick_words` decides which alias every name draws from, so every row
   re-renders. Never publish a cross-corpus delta as an improvement.
3. **Filter the slot, not the word**, and keep the fallback -- otherwise you trade a naming collision for an
   unnameable token.
4. **When one function filters a word class and its sibling does not, the sibling is the bug.** `_equip_word` had
   done it since P23; `pick_words` had not.
5. **A threshold with a comment defending it and no sweep behind it is a habit.** Thirty seconds of re-decoding
   found +0.011 of shipped accuracy sitting there.
6. **Move the floor in all four files or none.** `serve.py`, `measure_clauses.py`, `evaluate.py`, `eval_live.py`.
7. **Reported misses are not all model misses.** Check whether the sentence names a `planned` token before you
   touch a rule module.
8. **Reproducing P38.** Its checkpoint was overwritten by the P39 retrain and there is no backup. Put the
   `pick_words` name slot back to the unfiltered pool, regenerate (`--seed 20261108`) and retrain: `train.py`
   seeds `random` and `torch` from that same `--seed`, so a bit-identical corpus produces a bit-identical
   checkpoint, `541a625039746380baa8015c77bf2220`. If the hash differs, something else under `Tools/intent_model`
   has moved since P38 -- the P38 column would then describe a configuration with no file that produces it.

---

# PART 8.16 — Watch it in the game, then stop trusting the rule modules' happy path (P39/P40 verified)

PART 8.15 closed with 0.922 as played and a summary table. Every number in it came from
`measure_clauses.py` replaying a corpus. **Not one had been observed in the client that ships**, and
that gap hid two defects: one in the **build**, one in a **rule module the corpus had certified at
143/143**. Both are fixed here, and both are the kind a player finds in five minutes.

This part trains nothing. The checkpoint, the floor and the corpus are untouched; what changes is
what you are allowed to *claim*.

## Step 1 — A missing feature is usually a stale binary

The first PIE pass applied the right loadouts for every phrase and the log read
`Rules applied (intent model).` for all of them -- the row PART 8.13's own table calls *the header did
not arrive*. Read literally, that says the service is running a checkpoint older than P31 and the
provenance channel is dead. The service was fine.

Compare the artifact against the source it was built from:

| file | timestamp |
|---|---|
| `Binaries/Win64/UnrealEditor-WishCraft.dll` | 11/8/2026 11:25:22 PM |
| `Source/WishCraft/WishCraftPlayerController.cpp` | 11/16/2026 2:40:42 AM |
| `Binaries/Win64/UnrealEditor-WishCraft.patch_0.exe` | 11/16/2026 2:41 AM |
| `Binaries/Win64/UnrealEditor-WishCraft.dll` **after rebuild** | 11/17/2026 2:36:44 AM (1,170,944 bytes) |

The C++ was eight **days** newer than the DLL. The fix existed only in `patch_0.exe`: Live Coding had
applied it once, into a process that has since exited. **A Live Coding patch never writes the DLL** --
it is a module bound to one running process, so the next launch starts from whatever the DLL holds.

Hence the first rule of this part: **before diagnosing a missing feature, compare the binary's
timestamp to the source's.** `rebuild_and_restart_editor` is what puts C++ into the DLL. Live Coding
cannot, and no amount of re-patching will fix a clean launch.

## Step 2 — One session, all four layers

After the rebuild, every layer was exercised in the shipped client at the shipped floor of 0.50. Three
batches (six phrases, five, four; all accepted, none rejected), and the suffix finally resolved for
each layer:

| phrase | log line | layer |
|---|---|---|
| `nuke stealth sprint` | `Rules applied (intent model: model).` | the checkpoint |
| `attack, unseen, speed` | `Rules applied (intent model: model).` | the checkpoint |
| `shorter cooldowns and heavier fireballs` | `Rules applied (intent model: clauses).` | `clauses.py` |
| `give me a fireball without the speed boost` | `Rules applied (intent model: exclusion).` | `exclusion.py` |
| `banana milkshake` | `Warning: Intent keyword fallback: model abstained` then `Rules rejected: I could not find an ability I know in "banana milkshake". Try mentioning: Fireball, Invisibility, SpeedBoost.` | the C++ parser, loadout unchanged |

That last row is the fourth layer doing its job: the model abstained, the client parser ran, matched
nothing, and `ULoadoutValidator` refused without touching the loadout. **No dead layers, and the
dispatcher really does pick by evidence.**

## Step 3 — The exclusion rule had a hole in exactly one direction

The phrase above is the one that broke. It equipped **Invisibility** -- the only token the sentence
never mentions.

```text
give me a fireball without the speed boost   ->   Invisibility        (wrong: complement of {SpeedBoost})
```

`apply_exclusion` was collecting *every* candidate the sentence named and subtracting all of them. The
sentence names two, so `{Fireball, Invisibility, SpeedBoost} - {Fireball, SpeedBoost}` leaves the one
token with no mention at all. The intent was "subtract the excluded names", and the rule could not
tell an excluded name from a requested one.

**Why 143/143 never caught it.** `fam_exclude_without` only ever renders `without <the excluded
names>` -- there is no template that puts a wanted name before the marker. Every corpus row is
therefore the shape the old code got right, so the corpus was certifying the rule on its happy path and
nothing else.

The fix is to split the candidate list at the marker rather than treat it as flat
(`exclusion.py:92`):

```python
def _split_at_marker(words, marker):
    try:
        index = words.index(marker)
    except ValueError:
        return words, []
    return words[:index], words[index + 1:]
```

`kept` is now the candidates named **before** the marker, `excluded` those named **after**. If `kept` is
non-empty the answer is `kept - excluded`; only when nothing is named before the marker does it fall
back to the old complement. `Saved/probe_exclusion_sides.py` compares both shapes:

| sentence | was | now |
|---|---|---|
| nine corpus-shaped rows (`... without <excluded>`) | -- | **byte-identical**, no change |
| `give me a fireball without the speed boost` | Invisibility | **Fireball** |
| `let me vanish without speed` | Fireball | **Invisibility** |
| `give me a fireball except invisibility` | SpeedBoost | **Fireball** |

Nine unchanged and three fixed is the shape of a good fix: the measurement that certified the rule
still passes, and the rows it could never see now pass too.

## Step 4 — Two vocabularies, and the request's copy silently wins

The DataAsset held 5 / 5 / 6 keywords per ability. `catalogue.json` held 12 / 13 / 15. That did not
matter for training -- training reads the catalogue -- but it mattered enormously at serve time,
because `build_catalogue` lets the **request** override the catalogue's keyword lists. The shipped
client sends the DataAsset's short list, so **the model was being matched against a description it had
never trained on.**

`Saved/measure_served_keywords.py` scores the same 588 live rows both ways at floor 0.50:

| keywords from | token-set F1 | intent exact | empty answers |
|---|---|---|---|
| `catalogue.json` (what training saw) | 0.693 | 0.660 | 42 |
| the old DataAsset (what serving saw) | 0.694 | 0.658 | 51 |
| the aligned DataAsset | 0.693 | 0.660 | 42 |

On the corpus that is a wash -- 0.001 of F1, and nine extra abstentions nobody was counting. **The
damage was in the rules, not the model:** `vanish` was not a keyword in the asset, so `build_aliases`
could not resolve it, and `let me vanish without speed` fell through the exclusion layer into the old
complement `Fireball, Invisibility`.

So the two lists were aligned -- the asset now carries the naming surface, and it is one surface for
both the client parser and `build_aliases`:

| ability | keywords now in the DataAsset | count |
|---|---|---|
| Fireball | `fireball, fireballing, fire, ball of fire, blast, shoot, shooting, projectile, ranged, attack, nuke, offense` | 12 |
| Invisibility | `invisibility, invisible, vanish, unseen, ghost, cloak, cloaked, camouflage, sneak, stealth, stealthy, disappear, hide` | 13 |
| SpeedBoost | `speed, speedy, boost, haste, hasty, fast, sprint, running, zoom, velocity, turbo, mobility` | 12 |

SpeedBoost is deliberately 12 and not the catalogue's 15. **`faster`, `quick` and `quicker` are absent
on purpose** -- they are comparative adjectives that PART 8.15 spent a whole step removing from the name
slot, and putting them back as aliases re-introduces that defect at the client layer, where the rule
layers resolve names through this same list.

This is now an invariant, not a coincidence: **the naming surface is one list living in two files, and
nothing in the build makes them agree.** `Saved/check_keyword_parity.py` runs in the editor and checks
one exact contract:

```text
asset keywords  ==  catalogue keywords  minus  the banned adjectives
                    same order, same spelling, nothing added
```

Because SpeedBoost is the sanctioned case, "the two lists are equal" is the wrong test -- it would fail
forever. The script therefore treats those three as **allowed to be absent and forbidden to appear**.
Against the shipped pair it reports:

```text
token         cat  asset  missing from asset  extra in asset  order
Fireball      12   12     -                   -               ok
Invisibility  13   13     -                   -               ok
SpeedBoost    15   12     -                   -               ok
sanctioned absences: 3
PARITY OK -- 3 live tokens match /Game/Sandbox/DA_AbilityTokens, 3 banned adjective(s) correctly absent.
```

The first version of that script compared the two lists for equality and so failed on every run.
**A guard that always fails is a guard nobody reads**, which is why the contract, and not the
equality, is what got written down.

## Step 5 — The floor, proven in the client that ships

Every floor argument so far was a re-decode in Python. To prove it end to end, the service was run at
0.80 from `Saved/run_service_floor080.bat` (a launcher that pins `INTENT_CONFIDENCE=0.80` for the A/B
only), the phrases replayed, then `Saved/run_service.bat` restored the shipped 0.50.

| phrase | at 0.50 | at 0.80 |
|---|---|---|
| `nuke stealth sprint` | `(intent model: model)` -> `Fireball, Invisibility` | `(keyword fallback: model abstained)` -> `Fireball, Invisibility, SpeedBoost` |
| `fireball, invisible, hasty` | `(intent model: model)` -> `Fireball, SpeedBoost` | `(keyword fallback: model abstained)` -> `Fireball, Invisibility, SpeedBoost` |

The 0.50 answer is the model's. The 0.80 answer is **the keyword parser's** -- and note it is the
wrong set, because it equips everything whose name appears. That is the mechanism worth internalising:
raising the floor does not make the path safer, it hands the phrase to a dumber layer. *Which is also
why Step 4 matters more than the floor does.*

One side effect to re-measure, never assume: enriching the keywords raised scores enough that two
phrases used for band testing no longer discriminate -- `attack, unseen, speed` went 0.709 and
`mobility only please` 0.771, both now above 0.80. The replacement band, all measured after alignment:

| phrase | confidence | answer |
|---|---|---|
| `shoot Invisibility haste` | 0.721 | SpeedBoost, Fireball |
| `fireball, invisible, hasty` | 0.740 | Fireball, SpeedBoost |
| `only ball of fire, stealthy and zoom` | 0.767 | SpeedBoost |
| `nuke stealth sprint` | 0.771 | Fireball, Invisibility |

**A confidence you measured before a vocabulary change is stale.** Re-probe the band whenever either
list moves.

## Step 6 — Prove the fix did not move the numbers

`measure_clauses.py` again, after the `_split_at_marker` edit:

| | value |
|---|---|
| exclusion | **143/143** (`exclude_without` 73/73, `exclude_all_but` 70/70) |
| clauses | 210/210 |
| service | 0.929 / 0.912 / 0.994 / 0.993 |
| as played | **0.939 / 0.922** |
| declined | 18 |
| client-parser fixes | 6 (+0.010) |

Identical to PART 8.15, `EXIT 0`. A rule fix that leaves every corpus number untouched is the expected
result when the corpus cannot express the broken shape -- and it is also the proof that the fix did not
overreach into the rows that already worked.

## Step 7 — Driving PIE from Python, without guessing which client you are talking to

The verification was driven from Python, and the recipe is short enough to keep:

1. **Resolve the authoritative PC by outer package, not by name.** PIE worlds keep the map name
   (`Lvl_Arena`), so `GetName()` tells them apart from nothing. PIE identity lives in the outer package
   path: the client whose outer starts with `UEDPIE` is the editor's own world. Find the
   `WishCraftPlayerController` there, and confirm it is `IsLocalController()` -- otherwise
   `SubmitRuleText` runs on a proxy and the log you are tailing stays silent.
2. **Drive it on a tick, not from the call stack.** Register a `register_slate_post_tick_callback`
   closure, and hold it in a module-level variable so it is not garbage collected between frames.
3. **Gate on `bRuleRequestPending`.** The request is asynchronous: submit, then poll the flag until it
   clears. Reading the GameState's loadout before that gives you the previous phrase's answer.
4. **Leave about 20 frames between phrases.** One phrase per tick floods the HTTP layer and produces
   log lines you cannot attribute.
5. **Read the outcome from `Saved/Logs/WishCraft.log`, tailed afterwards** -- not from the return value
   of the submit. The suffix you are looking for is written by the completion handler.

`unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).editor_request_begin_play()` and
`..._end_play()` start and stop the session. `is_in_play_in_editor()` lags one frame behind the
request, so never assert the session is up on the same tick you asked for it.

## What PART 8.16 leaves you with

| | value |
|---|---|
| checkpoint | P39, MD5 `8866045eb4ad4014523a7437d6d51b6a`, epoch 16, 1,478,919 params -- **unchanged; nothing here trained** |
| shipped floor | **0.50**, unchanged, and now proven in the client |
| as played, live 3-token | 0.939 / **0.922** / 0.994 |
| rules | exclusion 143/143 **corpus-shaped** and 3/3 player-shaped after the fix; clauses 210/210 |
| verified in client | all four layers, once each, one PIE session, suffix resolved for each |
| naming surface | shipped 12 / 13 / 12 against the catalogue's 12 / 13 / 15; the 3 absences are the banned adjectives, and `Saved/check_keyword_parity.py` reports **PARITY OK** |
| biggest gap | unchanged: held-out detection 0.072 at 0.087 false positives |

## Pitfalls, in the order they will bite you

1. **A stale DLL presents exactly like a broken feature.** The log said "header did not arrive" and the
   service was correct the whole time. Timestamps first, always.
2. **Live Coding never writes the DLL.** It patches one process. Anything you want to survive a restart
   -- or to exist for a headless/verification run -- needs `rebuild_and_restart_editor`.
3. **"Certified on the corpus" means "certified on the corpus's shapes".** `exclude_without` only ever
   rendered `without <excluded>`, so 143/143 was 100% of a happy path. Ask what the generator *cannot*
   render before you call a rule complete.
4. **The naming surface lives in two files and the request's copy wins.** Training reads
   `catalogue.json`; serving reads whatever the client sent. A synonym missing from the asset makes
   `apply_exclusion` and `apply_clauses` silently fail to resolve it -- and `faster` / `quick` /
   `quicker` in the asset re-introduces PART 8.15's defect at the client layer.
5. **Raising the confidence floor does not make the path safer, it makes it dumber.** Below the floor
   the phrase goes to the keyword parser, which equips every name it can see. Parity between the two
   vocabularies is the real defence.
6. **An asset save returns `False` while PIE is running** and leaves the `.uasset` untouched, with no
   error worth reading. Stop PIE, re-apply, save, then verify the byte size moved.
7. **A driver closure that references a missing global spams `NameError` until the editor restarts.**
   Harmless, and it makes every subsequent log tail harder to read -- restart rather than filter.
8. **`is_in_play_in_editor()` lags the request by a frame,** so never assert play state on the same
   tick you requested it.
9. **Re-probe confidences after any vocabulary change.** Two band phrases stopped discriminating the
   moment the keywords were enriched, through no fault of the model.

---

# PART 8.17 — Most of the residual was not the model, and the numbers described a retired file (P41-P43)

PART 8.16 left 0.922 as played and a 52-row residual to work. Here it comes apart into three different
things wearing the same label: rows **no system could answer**, rows measured against **the wrong
vocabulary**, and one genuine rule defect. Nothing in this part trains. The checkpoint is still P39,
the floor is still 0.50, and `model.pt` still hashes to `8866045e...`.

## Step 1 — Separate "read this wrong" from "cannot be asked this at all"

Ten of the 52 rows were being scored as model errors while being unanswerable by construction. Their
exclusion names only tokens the game never had -- `LightningStrike`, `Shield` -- against a live set of
three. `apply_exclusion` finds no live name to subtract, returns `None`, the model abstains, and an
empty token set then scores as three misses.

That is a defect in the **measurement**, not in the system, and the fix is to let the measurement say
so:

- `exclusion.py` gained `mentioned()` -- the same whole-word alias scan `apply_exclusion` runs, but
  with no candidate filter, so a caller can distinguish "cannot be asked" from "answered wrongly".
- `measure_clauses.py` gained `unshippable()` and an extra report block listing those rows as their own
  subset.

The first version of that filter was wrong, and the run is what caught it: flagging any row that merely
*named* a planned token selected **153 of 153** live exclusion rows. Naming one in the excluded
position is harmless -- `without lightning and fireball` resolves on `fireball` and subtracts it, which
is the correct label. The artefact is only a sentence whose exclusion has **nothing live to work with**.
That criterion selects exactly **10** rows (6 `exclude_without` + 4 `exclude_all_but`), which is the
number the previous residual predicted. When a filter reproduces an independently-estimated count, the
criterion is probably right.

The tempting alternative was to restrict the generator's candidate pool to the three live tokens. That
re-renders all four splits, moves both test hashes, and voids every published number -- to fix ten rows
no rule or model change could ever answer. Measurement-side fix, checkpoint untouched.

## Step 2 — A service that is not running looks exactly like a model that is broken

Nothing was listening on 8077. Every rules box in that state falls through to the client's keyword
parser, which is a *supported* behaviour, so the game does not error -- it just quietly stops being a
demonstration of the model. Before any PIE test: `Saved/run_service.bat` in its own window. The probe
that exercises all four layers is `Saved/probe_layers_p41.py`, and note that a backgrounded service dies
with the session that started it.

## Step 3 — The client contradicted the probe, and the client was right

PIE said `nuke stealth sprint` -> **Fireball + Invisibility**. The HTTP probe on the same phrase could
not resolve `nuke` at all. When those two disagree the client wins, because the client is what ships.

The cause was a third copy of the vocabulary: `catalogue_live.json` still held the **5 / 5 / 6** keyword
lists -- exactly the ones P40 replaced in the DataAsset. Every live figure in PART 8.16, including
0.922, had been measured against a vocabulary the game had stopped using.

Fixed the way P40 fixed it, except there is now no copy left to drift: `measure_clauses.served_catalogue()`
derives the served lists from `catalogue.json` in code, and they were verified against
`/Game/Sandbox/DA_AbilityTokens` -- **Fireball 12 / Invisibility 13 / SpeedBoost 12, 0 mismatches**.

| reading (served vocabulary) | rows | token set | intent |
|---|---|---|---|
| service | 588 | 0.929 | 0.913 |
| as played | 588 | 0.935 | 0.920 |
| as played, expressible only | 578 | 0.952 | 0.936 |

The correction was only **0.002**, but read the direction: enriching the keywords made the client's
keyword parser **74% better** (0.355 -> 0.617) and the composed path slightly **worse**. The client
fallback equips every name it sees, so a stronger fallback over-equips. The composed path is bounded by
how often the model abstains, not by how far the keywords reach -- which is why "improve the keyword
list" is not an accuracy strategy.

Both vocabulary blocks print, old first, so nothing already published moves.

## Step 4 — Re-read the residual on the vocabulary that ships, and read composition not totals

On the served vocabulary the residual was **51 of 588** -- barely different from 52, which is why the
total alone is a bad instrument. The composition was the useful part:

| family | rows | missed abilities | invented abilities |
|---|---|---|---|
| `allow_terse` | 12 | 12 | 1 |
| `request_can_we` | 9 | 12 | 0 |
| `only_prefix` | 6 | 9 | 0 |
| `param_single_superlative` | 4 | 0 | **4** |
| `exclude_without` + `exclude_all_but` | 10 | 30 | 0 (unanswerable) |
| `category_verb` | 1 | 1 | 0 |

Three things fell out of it:

- `request_can_we` 13 -> 9: the enriched keywords let the client parser answer more abstentions.
- `param_single_superlative` 2 -> 4, and **every one invents `SpeedBoost`** -- `make the projectile
  speed much lower` was being read as the ability named *SpeedBoost*.
- `nuke stealth sprint` now reads as missing only `SpeedBoost`, and PIE produced exactly
  `Fireball + Invisibility`. The measurement finally described the game.

An invented token is worse than a missed one: it equips something the player never asked for. That made
those 4 rows the only thing worth fixing -- and it was one family with one cause.

## Step 5 — The rule belonged to the phrase, not to one branch of it

`apply_clauses` computed "is this a one-clause sentence?" **inside** its exactly-one-name branch. The
ambiguous branch -- the one that fires when `speed` names both `SpeedBoost` and half of Fireball's
`ProjectileSpeed` parameter -- called `_resolve_for` with the default `min_params=2`. So a
conjunction-free phrase was asked to resolve **two** parameters, both candidates failed, the rule
refused, and the model answered `SpeedBoost`.

The fix is a hoist, not a new rule: `single` is now computed above both branches. The ambiguous branch
still requires exactly one surviving candidate, so the precision guard is untouched.

| | before | after |
|---|---|---|
| clause rule fired | 210 (210/210) | **221 (221/221)** |
| `param_single_superlative` wrong | 4 of 113, all invented | **0 of 113** |
| served residual | 51 of 588 | **45 of 588** |
| served as played, intent | 0.920 | **0.930** |
| served expressible, intent | 0.936 | **0.946** |
| wrong buckets | 9 | **7** |

No regressions: exclusion 143/143, `param_multi_agree` 108/108, `undetermined` 0, `EXIT 0`. Both
vocabulary blocks now print the *same* composed figures, which is the signature that the vocabulary
difference had only ever been routed through rows the clause rule now answers.

Two operational details, both bitten once: `uvicorn` has **no `--reload`**, so a service started before
the fix keeps running the old `clauses.py` -- restart it, then re-probe; and the probe itself was still
built from the retired `catalogue_live.json`, so it had to be pointed at the served catalogue before its
output could be compared to PIE phrase for phrase.

## Step 6 — Stop, and be able to say why

What is left is **35 rows of 578**: `allow_terse` 12, `request_can_we` 9, `only_prefix` 6,
`category_verb` 1, plus 7 wrong buckets. All but one are names **missing from terse comma lists** --
the model's own territory, not a rule defect. Retraining for 35 presence misses this close to a
submission trades a known quantity for an unknown one. The model is frozen at P39.

## What PART 8.17 leaves you with

| | value |
|---|---|
| checkpoint | P39, `8866045eb4ad4014523a7437d6d51b6a` -- unchanged; nothing here trained |
| quotable figure | **0.930 as played / 0.946 expressible, of 578 rows, 3-token served set** |
| residual | 45 of 588, of which 10 unanswerable by construction -> 35 real |
| invented tokens | 0 (`param_single_superlative` 0 of 113) |
| rules | exclusion 143/143, clauses 221/221, `param_multi_agree` 108/108 |
| new measurement machinery | `exclusion.mentioned()`, `measure_clauses.unshippable()` / `served_catalogue()` |
| decision recorded | fix the measurement, never the corpus, when the row is unanswerable |

## Pitfalls, in the order they will bite you

1. **Three copies of a vocabulary is two too many.** P40 found the DataAsset drifted from
   `catalogue.json`; P42 found `catalogue_live.json` had too. Derive the served lists in code.
2. **When the client and your probe disagree, the client is the specification.** PIE resolved `nuke`
   while the probe could not; the probe was wrong.
3. **A residual total hides everything you need.** 52 -> 51 was noise; the family table showed two
   families moving in opposite directions.
4. **Watch for invention separately from misses.** One invented token is worse than ten misses, and it
   was the only thing in the residual caused by a rule rather than by vocabulary.
5. **A rule that asks a phrase for two things when it named one will look like a model error.** The
   refusal is quiet, the fall-through is confident.
6. **`uvicorn` does not reload.** A "fix that did not work" is usually a process that never restarted.
7. **A filter that selects everything is a filter with the wrong criterion,** not a data problem. The
   first `unshippable()` flagged 153 of 153 rows.

---

# PART 8.18 — Make the answer visible, because a camera cannot read the log (P44, P44a)

Everything in PARTs 8.1-8.17 was verified through logs, headers and corpora. That is sound evidence and
worthless to a viewer. A recording of the shipped feature showed a text box, a status line, and then
nothing -- while the interesting output (which layer answered, which abilities got equipped, what the
parameters became) sat in the log.

## Step 1 — Name the gap before decorating it

The honest inventory: `WBP_RuleInput` was already presentable (dark rounded panel, title, hint text,
Send, status line that reports applied/rejected); the VFX existed (`NS_Ability_FireballCast`,
`NS_Ability_InvisibilityPulse`, `NS_Ability_SpeedTrail`, `NS_Damage`); `Lvl_Arena` had 26 static meshes,
two player starts, lights and a navmesh -- and no targets. So the missing thing was **not art**, it was
that the headline feature produced nothing a camera could see.

## Step 2 — The readout reads state that already exists

`/Game/Sandbox/UI/WBP_LoadoutReadout`, top-right, using the same border brush as the rule panel so the
two read as one HUD:

- a title (`Active Rules`);
- a **layer line** showing the raw resolution message -- `Rules applied (intent model: clauses).`,
  `... exclusion.`, `... model.`, or `... keyword fallback: model abstained.` The layer name is already
  inside that string, so there is nothing to parse and nothing to keep in sync;
- one line per equipped ability with its clamped values: `Fireball - Damage 40 . Cooldown 0.75`.

**No C++ was needed.** `GetLoadoutManager()` was already `BlueprintPure`, and `OnRuleResolution` /
`OnLoadoutMessage` were already `BlueprintAssignable`, so UMG reads `Loadout -> FAbilitySpec.Token /
Params -> FAbilityParam.ParamName / Value` directly and refreshes on every ruleset commit.

Two defects were caught by **rendering** the widget rather than reading its tree: Size To Content
shrank the panel to 389 px (and would have re-flowed it mid-take as the text changed) -- width pinned;
and a fixed 180 px height clipped the third ability the moment two lines wrapped -- it is 560 x 232,
which holds three abilities at two wrapped lines each.

## Step 3 — It compiled clean and did nothing (P44a)

The spawn had three faults that all compiled without a warning:

1. `Create Widget` had been appended **inside** the pre-existing `Branch (bRuleRequestPending) == True`,
   which is false at BeginPlay -- so the node was dead code on the only path that mattered;
2. the once-only guard then called `Is In Viewport` on the still-null `CreatedReadout`, raising
   **Accessed None** and halting execution;
3. nothing assigned the return value or called `Add To Viewport`.

All three look like a layout bug and none of them are. The fix is structural: create on the **main**
Construct chain and guard on validity rather than calling into a possibly-null value.

The diagnostic that isolated it was a hand-created copy of the widget, which had worked all along --
when a hand-placed instance works and the spawned one does not, stop reading the panel and read the spawn.

## Step 4 — Read the strings at runtime, not by eye

Verified in PIE, reading the live widget:

| phrase | layer line | ability line |
|---|---|---|
| (spawn) | `waiting for a rule` | `no abilities equipped` |
| `shorter cooldowns and heavier fireballs` | `Rules applied (intent model: clauses).` | `Fireball - Damage 40 . ProjectileSpeed 2,500 . Cooldown 0.75` |
| `give me a fireball without the speed boost` | `Rules applied (intent model: exclusion).` | `Fireball - Damage 20 . ProjectileSpeed 2,500 . Cooldown 1.5` |

Two details worth knowing before a take. `ProjectileSpeed 2,500` carries a thousands separator because
`ToText` groups by locale -- consistent, not a defect. And the ability line lists **every** parameter,
not only the one the sentence mentioned: `Damage 20` vs `40` across those two runs is visible proof that
the whole ruleset was rebuilt rather than patched.

## What PART 8.18 leaves you with

| | value |
|---|---|
| on-camera claim | the answering layer and the resolved parameters, per phrase, without a log |
| A/B takes now recordable | same phrase into clauses, into exclusion, and one that abstains into the keyword fallback |
| C++ required | none -- the delegates and the pure getter already existed |
| where it lives | `/Game/Sandbox/UI/WBP_LoadoutReadout`, spawned once from `WBP_RuleInput`'s Construct |

## Pitfalls, in the order they will bite you

1. **A Blueprint that compiles clean can be dead code.** Check which branch your new node landed under,
   not merely that it exists.
2. **`Is In Viewport` on a null widget raises Accessed None and stops the chain.** Guard on validity.
3. **Render the widget at several content sizes.** Fixed heights clip, and Size To Content re-flows
   mid-take.
4. **A hand-placed instance is a free control experiment.** If it works and the spawned one does not,
   the bug is in the spawn.

---

# PART 9 — Reproducing every number

From the project root. The shell used here has a 120 s cap, so long runs are backgrounded and
polled; `-u` is mandatory or Python buffers the log into silence.

```bat
:: regenerate the corpus. Refuses to write if the audit gate fails
.venv\Scripts\python.exe Tools\intent_model\generate_dataset.py --seed 20261108

:: audit what is on disk, independently of the generator; exits non-zero on any defect
.venv\Scripts\python.exe Tools\intent_model\dataset_audit.py

:: train. Defaults are 30 epochs / patience 100; P39 is best epoch 16, ~30 min on CPU
.venv\Scripts\python.exe Tools\intent_model\train.py

:: score it. Defaults to the SHIPPED floor (0.50) on purpose
.venv\Scripts\python.exe Tools\intent_model\evaluate.py

:: the two questions the bucket head is really being asked
.venv\Scripts\python.exe Saved\probe_heads.py

:: does any decode threshold beat the trivial predictor?
.venv\Scripts\python.exe Saved\sweep_moved.py

:: what the game actually ships: checkpoint row, then both deterministic rules
cd Tools\intent_model && ..\..\.venv\Scripts\python.exe eval_live.py

:: the clause rule alone, the composed service path, and the as-played path
:: long run -- background it, because this shell dies at 120 s
Saved\run_measure.bat

:: or in the foreground, from the module directory
cd Tools\intent_model && ..\..\.venv\Scripts\python.exe measure_clauses.py

:: where the live misses are, by kind and by family
.venv\Scripts\python.exe Saved\audit_live_failures.py

:: are the misses unseen vocabulary or structural?
.venv\Scripts\python.exe Saved\audit_live_oov.py

:: which layer answers each probe phrase (service must be running). Prints X-Intent-Source
.venv\Scripts\python.exe Saved\verify_intent_source.py

:: both sides of the exclusion marker: corpus-shaped rows and player-shaped ones
.venv\Scripts\python.exe Saved\probe_exclusion_sides.py

:: what the keyword list the CLIENT sends costs against the one training saw
.venv\Scripts\python.exe Saved\measure_served_keywords.py

:: the naming surface: catalogue.json vs DA_AbilityTokens. Run this one inside the editor
:: (execute_unreal_python on Saved\check_keyword_parity.py) because the asset is binary.
:: It must print PARITY OK -- anything else is drift, and drift is silent in play.
```

Backgrounded form, and the polling idiom:

```bat
start "" /b cmd /c ".venv\Scripts\python.exe -u Tools\intent_model\train.py > Saved\train.log 2>&1"
ping -n 90 127.0.0.1 >nul && type Saved\train.log
```

`timeout /t` does not work here ("Input redirection is not supported"); `ping` is the sleep.

## The reference run — read the version stamp before diffing

There are two reference blocks in this part and they belong to different corpora. Confusing them is the
fastest way to "reproduce" a number that no longer exists.

**Corpus v3, P39 — current.** The live reference is the header and rule lines of
`Saved/measure_clauses.log`:

```text
live candidates : Fireball, Invisibility, SpeedBoost
test rows       : 588 of 1166 kept
undetermined    : 0 rows whose text does not determine its label
ceiling         : 1.000 intent exact is the most this split can score, by any system
overlap         : 0 rows fired by both rules (should be 0)

exclusion rule               fired  143 rows, right on  143  (1.000)
clause rule                  fired  210 rows, right on  210  (1.000)

live 3-token -- service (exclusion + clauses + model)
  rows                 : 588
  token set exact      : 0.929
  intent exact         : 0.912
  bucket accuracy      : 0.994
  bucket acc | tok ok  : 0.993

shipped misses, split by what is actually wrong
  wrong token set          :  42  (presence / detection)
  right set, wrong bucket  :  10  (level / direction)
  total                    :  52 of 588
```

**Corpus v1, P24 — historical.** The `eval_live.py` block below was measured 2026-11-16 14:30, against
MD5 `3eb0c428402a9691eddaf206f91985d2`. P38 replaced the corpus, the `much_lower` wording and the
checkpoint in one pass (PART 8.14), so `537`, `131`, `198`, `0.756` and that hash are no longer in the
repo. It is kept because the *shape* of the block is what you are learning to read.

```text
live candidates : Fireball, Invisibility, SpeedBoost
test rows       : 537 of 1166 kept
dropped         : 629 label an ability the game cannot equip

live 3-token -- keyword baseline
  rows                 : 537
  token set exact      : 0.339
  intent exact         : 0.128
  bucket accuracy      : 0.797
  bucket acc | tok ok  : 0.723

live 3-token -- intent model
  rows                 : 537
  token set exact      : 0.594
  intent exact         : 0.391
  bucket accuracy      : 0.888
  bucket acc | tok ok  : 0.870

live 3-token -- shipped (exclusion + clauses + model)
  rows                 : 537
  token set exact      : 0.881
  intent exact         : 0.756
  bucket accuracy      : 0.950
  bucket acc | tok ok  : 0.952
exclusion rule fired on 131 rows
clause rule fired on 198 rows

live 3-token -- by family
  family                            rows  kw exact  mdl exact  mdl tok  mdl bkt
  allow_terse                         60     0.283      0.517    0.700    0.947
  category_verb                        6     1.000      0.833    0.833    1.000
  exclude_all_but                     73     0.000      0.000    0.000    0.945
  exclude_without                     65     0.000      0.000    0.000    0.938
  only_prefix                         60     0.400      0.733    0.883    0.959
  param_multi_agree                   98     0.000      0.296    0.806    0.664
  param_single_superlative           110     0.000      0.645    0.900    0.864
  request_can_we                      65     0.338      0.462    0.631    0.918
```

Five lines carry the whole assertion, and each has a current value. The left column is what the block
above prints; if you are reproducing today you are reading the right column.

| Line | corpus v1 (P24) | corpus v3 (P39, current) | If it differs |
|---|---|---|---|
| `test rows` / `dropped` | `537` of `1166`, `629` | `588` of `1166` | the corpus changed, or the live candidate list did |
| `exclusion rule fired on …` | `131` | `143` | `exclusion.py` changed |
| `clause rule fired on …` | `198` | `221` | `clauses.py` changed (210 before P43) |
| shipped `token set exact` | `0.881` | `0.942` served / `0.929` legacy | one of the three layers, or the checkpoint |
| shipped `intent exact` | `0.756` | **`0.930`** served / `0.912` legacy | same |

Two vocabularies print, so read the header before the number. The block above is the **legacy**
printout, still emitted for comparability; the served block is the one to quote, and PART 8.17 is why
the two exist at all. On the served vocabulary the residual is `45 of 588`, of which 10 rows name only
tokens the shipped set never had -- `unshippable()`, not a miss. Reproduce those two subsets with the
same command; do not add a third script.

Nothing in these commands samples: the corpus comes from a fixed seed, the checkpoint is a fixed file,
and `exclusion.py` / `clauses.py` are deterministic. Every number is an assertion rather than a typical
value — if one differs, the corpus, the checkpoint or a rule module differs. There is no fourth
explanation.

Then confirm the checkpoint before blaming any code:

```bat
certutil -hashfile Tools\intent_model\model.pt MD5
:: must print 8866045eb4ad4014523a7437d6d51b6a   (P39)
:: P24's 3eb0c428... is history only, kept at Saved\model_p24_backup.pt
:: P38's 541a6250... was overwritten by the P39 retrain; reproduce it only by
:: reverting the pick_words filter and retraining (PART 8.15)
```

The hash matters more than it used to. `INTENT_CONFIDENCE` is now a knob that changes the reported
numbers without touching the checkpoint, so a mismatched hash and a mismatched floor look identical from
the log alone — read `/health`, which prints both.

Two reproductions are **not** current: `eval_live.py`, `Saved/probe_heads.py` and `Saved/sweep_moved.py`
were never re-run on corpus v3 and still print v1 numbers. `measure_clauses.py` and `evaluate.py` are the
v3 references, and `Saved/sweep_floor.py` is the one that decides the confidence floor.

The by-family block is diagnostic, not an assertion to defend. `exclude_all_but` and
`exclude_without` both read `0.000` on `mdl exact` **and** `mdl tok` — the model cannot express a
complement set at all — while their `mdl bkt` stays at `0.945` and `0.938`, because the parameters
those phrases move are ordinary. Buckets fine, token set zero is what a working complement rule looks
like from the model's side, and it is the most common "is my training broken?" misread. The genuinely
weak family is `param_multi_agree` at `0.664` bucket accuracy.

*(Those are v1 numbers. On corpus v2 `param_multi_agree` is `102/102` exact, and the remaining weakness
moved to the token-set head: `allow_terse` and `param_single_superlative` account for 31 of the 62
wrong-token-set rows, almost all of them the `faster` / `quicker` / `fast` collision, while their bucket
accuracy stays at 0.976 and 0.952.)*

**One trap in these tables.** `measure_clauses.py` prints a by-family block with the *same four
headers* and different contents: it passes the model-alone score as `print_families`' first argument
and the as-played path (service, then the client's keyword fallback) as the second, so its `kw exact`
column is the model and its `mdl *` columns are
what ships. The header line now says so in words. Reading `exclude_all_but` as `0.000` then `0.781`
there is "model alone, then shipped" — the comparison the rules exist to move, not a contradiction of
the `0.000` above.

---

# PART 10 — File map

| File | Role |
|---|---|
| `catalogue.json` | exported from `DA_AbilityTokens`; the token vocabulary and parameter ranges |
| `schema.py` | `BUCKETS`, `bucket_to_value`, cap validation, `DEFAULT_INDEX` / `MOVED_*` mapping |
| `generate_dataset.py` | samples intents, renders sentences through families, self-audits, splits by family, and refuses to write a corpus the audit rejects |
| `dataset_audit.py` | the two defect classes a sentence cannot express (`label not in text`, `same words, two labels`), `BUCKETLESS_FAMILIES`, and the pre-write gate `generate_dataset.main()` calls. Stdlib only, so the generator can import it |
| `train.py` | two-tower encoder, trigram tokenisation, presence head, factorised bucket head, checkpoint |
| `evaluate.py` | keyword baseline port, per-family table, `bucket_head_report`, held-out token report |
| `serve.py` | FastAPI `POST /rules`; `INTENT_CONFIDENCE` default 0.50; empty list means fall back; runs the exclusion rule, then the clause rule, then the model, and names the answering layer in the `X-Intent-Source` response header |
| `exclusion.py` | deterministic complement for `without` / `except` / `all ... but` / leading `no`; token names only, buckets stay `default` |
| `clauses.py` | deterministic clause rule for `and` / `,` / `but`, plus single-clause phrases that name one ability and one parameter; token names and bucket labels only, else `None` |
| `measure_clauses.py` | fire count, precision, per-family split and every wrong row for **both** rules; also prints each rule's accuracy on the rows whose text actually determines the label, the split's exact ceiling, and why each miss is undetermined (`label not in text`, `same words, two labels`). Then the composed path: `diagnose_shipped()` splits its misses into wrong-token-set vs wrong-bucket, `fallback_gain()` accounts for the client's last resort, and `apply_client_fallback()` scores the game (service, then keyword parser) separately from the service |
| `eval_live.py` | scores the 3 shipped tokens, reporting the checkpoint and the shipped rule+model path separately |
| `model.pt` | checkpoint + vocabulary, read by `serve.py` and every probe |
| `Saved/verify_intent_source.py` | read-only HTTP probe: posts four phrases in the controller's own body shape and prints the `X-Intent-Source` header beside the returned token names |
| `README.md` | the honest short version: numbers, defects, reproduce commands |

**Invariants — break these and the numbers above stop meaning anything:**

- The model emits **buckets and token names only**. It never writes a parameter value and
  never bypasses `ULoadoutValidator`.
- `BUCKETS` order is index identity. Do not reorder it.
- `bucket_probs` stays a normalised five-way distribution; every call site `argmax`es it.
- **No class weighting in the bucket loss.** Masking the row set is the replacement.
- **The bucket is never pinned by a split.** If a family forces a bucket, training sees exactly
  one direction and the test family measures an unseen *concept*. Vary the wording, not the
  label. This bug cost a whole retrain cycle and is now enforced by `prepare()`.
- `evaluate.py` defaults to the same confidence floor `serve.py` ships.
- The `/rules` response body holds **exactly one** root field, `tokens`. Two guards depend on it
  (`WishCraftPlayerController.cpp:204` recognises the abstain sentinel by it,
  `LoadoutValidator.cpp:144` rejects the response without it), so any metadata -- the answering
  layer included -- travels in a header.
- Judge on `test.jsonl` / `golden.jsonl`, never on `valid.jsonl`.
- **A label the sentence does not determine is corpus noise, not model error.** P38 removed the last of
  them: `prepare()` pins `mentioned_count=0` for `BUCKETLESS_FAMILIES`, `much_lower` has wording of its
  own, and `fam_param_multi_together` offers `both ...` only when its two buckets agree. Corpus v2 has
  **0** such rows and a live ceiling of **1.000**, against 76 rows and `0.858` under v1. The invariant is
  now the gate rather than the count: `generate_dataset.main()` refuses to write a corpus that
  `dataset_audit.find_problems()` rejects. `BUCKETLESS_FAMILIES` stays a literal list of the families
  `prepare()` has no branch for; if you add a branch there, remove the family from it.

---

# PART 11 — Glossary

**Bucket** — a symbolic parameter change (`higher`, `max`), converted to a number by C++.
**Catalogue** — the exported token table; the model's entire vocabulary.
**Confidence floor** — the threshold below which the service returns `{"tokens": []}` so the
caller falls back to keywords.
**Direction head** — the four-way head that answers *which way* once a parameter has been
judged to move.
**Family** — a template group in the corpus; the unit of the train/test split.
**Held-out token** — a catalogue row excluded from training entirely (`Blink`), used to
measure zero-shot behaviour.
**`moved` head** — the binary head answering *does this parameter change at all*.
**Presence head** — scores candidate tokens against the phrase, with the candidate set in
context so complements are expressible.
**Trigram fallback** — every word also enters as character trigrams, so unseen ability names
are not `<unk>`.
**`valid.jsonl`** — the reduce-only split. Useful for early stopping, **useless for judging**.
**Layer** — one of the three things that can answer `/rules`: `exclusion.py`, `clauses.py`, or the
checkpoint. The one that answered is reported in the `X-Intent-Source` response header, never in the
body.
