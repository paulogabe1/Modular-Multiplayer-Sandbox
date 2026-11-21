# Intent model

A small, purpose-trained model that turns a free-text rule into **ability names plus
parameter buckets**. It is not a language model bolted on for novelty: it exists because
`URuleParserLibrary::ParseRuleText` is pure substring containment, so it has no negation
and no parameters at all.

The model's entire output surface is:

```json
{"tokens": [{"name": "Fireball", "params": {"Damage": "higher", "Cooldown": "higher"}}]}
```

It never emits a gameplay value. `ULoadoutValidator` on the C++ side stays the only thing
that writes a spec, and `schema.bucket_to_value` maps `higher` to `2 x default` and clamps.

---

## Read this first: the model alone only just matches the keyword baseline

This is the headline result and it is not flattering, so it goes at the top rather than in
a footnote. Scored on `test.jsonl` (1,166 rows, 8 template families held out whole),
**corpus v3 / checkpoint P39**, decoded at the shipped confidence floor of 0.50:

| | token-set exact | intent exact | bucket accuracy |
|---|---|---|---|
| keyword baseline (shipped parser) | **0.651** | 0.407 | 0.889 |
| intent model (P39 alone) | 0.607 | **0.408** | **0.930** |

On intent exact the model now sits **0.001 above** the baseline where P38 sat 0.067 below it, and on
the set the game actually ships (three live tokens, below) it clears the baseline outright:
0.415 against 0.160. The token-set shortfall that remains is the `exclude_*` complement families,
which a substring matcher cannot express at all and which `exclusion.py` answers instead.

**Nothing in this section is comparable with anything published before P38.** Corpus v2 was
regenerated from a corrected generator, and corpus v3 changed which words each ability is *called*,
so every sentence in all four splits is different and the 537-row figures describe an exam that no
longer exists. The v1 table read `0.664 / 0.314 / 0.858` against `0.527 / 0.326 / 0.907`; both
columns are retired. See *The service scores 0.912* below and tutorial PART 13.

What **is** comparable is each metric against its own constant on the same rows, because that
constant is recomputed from the data it is scored against:

| | model | trivial baseline |
|---|---|---|
| moved (did this parameter move at all?) | **0.941** | 0.889 (always `default`) |
| direction (which way?) | **0.709** | 0.469 (always-most-common) |

`moved` clears its constant by 0.052. Through P24 it read 0.823 against 0.858 -- the head
moved parameters slightly too readily -- and the silent-bucket rows are what taught it to: 918 of
the 9,878 corpus-v1 training labels asserted a bucket the sentence never mentioned, and the only way
to fit them was to invent intensity. Fixing the answer key fixed the head.

P23 fixed the simplest sentence: naming an ability in order to **equip** it used to change its
stats -- `fireball` returned ProjectileSpeed `lower` (2500 -> 1250 for free) on P20 -- because all
3,745 single-ability `param_*` rows in the corpus moved a parameter and not one of them was an
equip request. P24 then unblocked a starved direction: `param_speed_contrast` was asking for 643
rows and realizing 312, so the downward half of `slower` had almost no mass behind it. Both were
counted before they were changed; see `Docs/BuildJournal.md` P23 / P24 and tutorial PART 8.6 / 8.7.

The abstain behaviour from P16 still holds: `make me a sandwich` returns an empty token list, not
SpeedBoost at 1.000 as it did through P14.

It still loses on `test.jsonl` token-set exact (0.607 against 0.651) — that gap is the `exclude_*`
complement families, which the baseline cannot express at all — and matches it on intent exact
(+0.001, was -0.067 at P38). That is not the model getting worse or better by accident: a third of
the rows that flattered a substring matcher were rows whose label was wrong, and corpus v2 relabelled
them honestly. What actually moved is `moved` and `direction` above, and the shipped path below.

Read this table knowing that the test split was silently moving until P16. All three splits used to
draw from one seeded RNG stream, so adding a training family changed the test sentences and every
comparison across P13/P14/P15 was scored against a slightly different exam. Each split now has its
own stream, verified by hashing `test.jsonl` with and without the abstain families — identical.

On `golden.jsonl` (20 hand-written sentences) the model **loses** on intent exact, 0.200 against
the baseline's 0.250, and wins bucket accuracy 0.871 against 0.839, `moved` 0.892 against 0.839 and
`direction` 0.867 against 0.467. Twenty rows means one row is worth 0.05, so treat the intent gap as
unresolved rather than as a verdict — `direction` gained 0.200 on this file against P24's 0.667, and
that is the largest single move in it.

The baseline is a faithful port of `ParseRuleText`: lowercase, strip spaces/hyphens/
underscores, substring-match each token's name or keywords. **It wins token-set exact by a wide
margin and it has taken intent exact back too.** The reason it can is structural: the generator
builds each phrase out of the catalogue's *own keyword lists*, so the baseline is handed the
word→ability mapping that the model has to learn from examples. What it still cannot do is express a
parameter — it is pinned to all-`default` and scores **exactly 0.000** on every parameter family.

Three caveats about comparing runs. The corpus is regenerated whenever the generator changes,
so row contents are not identical between checkpoints and per-family figures move for reasons
unrelated to the model. Treat the *structural* metrics — `moved` and `direction` from
`bucket_head_report` — as the honest comparison, because each carries its own constant
baseline computed on the same rows.

Third: held-out `Blink` is currently **0.222 detection / 0.151 FPR** (P16: 0.152 / 0.136). Detection
is back up and the false-positive rate is flat, which is the direction that matters for the
vocabulary-agnostic claim: the model still finds a token it has no training signal for about one time
in five, at a cost of about one spurious control row in seven. Reproduce the pair with
`evaluate.py` and `Saved/probe_blink.py`.

Two things make that less dismissible than it looks, but neither is an excuse:

1. The corpus generator builds each phrase out of the catalogue's *own keyword lists*.
   The baseline is handed the word→ability mapping; the model has to learn it from 8,143
   examples. This is a real confound in the benchmark, not a property of the model.
2. The baseline can never express a parameter — it is structurally pinned to all-`default`.
   It scores **exactly 0.000** on every parameter family. Whatever else is true, it cannot
   do the job the model exists for.

**Therefore: the model is not a drop-in replacement for the parser.** The shipped pipeline
keeps the parser as the primary path.

---

## The service scores 0.912 on the live set; the game scores 0.922

`test.jsonl` is scored on all six catalogue tokens. The game ships three, which is 588 of the 1,166
rows on corpus v3.

Two different compositions exist here and they must not be quoted interchangeably. `serve.py` runs
three layers. The game runs four: `WishCraftPlayerController.cpp:212-215` treats an empty `tokens`
array as "the model abstained" and re-answers the phrase with `URuleParserLibrary::ParseRuleText`, so
the client's own keyword parser is a fourth layer that lives in C++, not in this directory. Every
figure published before P36 described the service and silently understated the game.

| live 3-token (588 rows) | token-set exact | intent exact | bucket accuracy | bucket acc / token ok |
|---|---|---|---|---|
| keyword baseline | 0.355 | 0.160 | 0.837 | 0.750 |
| intent model (P39 checkpoint alone) | 0.660 | 0.415 | 0.898 | 0.855 |
| service (exclusion + clauses + model) | 0.929 | 0.912 | 0.994 | 0.993 |
| **as played (service, then client fallback)** | **0.939** | **0.922** | 0.994 | 0.993 |

**The ceiling on this set is now 1.000.** On corpus v1 it was 0.858, because 76 of the 537 rows
carried a label their text could not express. Corpus v2 removed them, so intent exact and "accuracy
on the rows whose text determines the label" are the same number, and every remaining miss is a real
one. `measure_clauses.py` prints both readings side by side precisely so that the two can never be
silently added together again.

18 of the 588 rows reach the client empty. The parser is exactly right on **6** of them and wrong on
**0** -- it cannot be otherwise, since the fallback only fires where the service returned nothing.
Those 6 are `request_can_we` 5 and `category_verb` 1.

Deliberately absent: a server-side fallback. The client already runs one, and consuming the empty
answer on this side would delete the `model abstained` log line, which is the only evidence that the
confidence floor is what silenced a phrase.

Most of that gain is **not** a better model. It is two deterministic rules answering phrases the
model structurally cannot:

- `exclusion.py` -- **143 rows, 143 right (1.000) on corpus-shaped rows**, which is 100% of a happy path
  and not completeness: a phrase naming a *wanted* ability before the marker returned the complement until
  P40 fixed it (see *Verified in the game* above). The phrase asked for a complement ("without the
  fireball", "all abilities but the fireball") and the rule answers with set subtraction instead of
  letting the model equip the ability the sentence named *in order to remove* it. Every bucket it
  emits is `default`, so bucket accuracy does not move on those rows at all. On corpus v1 this read
  104 of 131 (0.794) and the missing 27 were all mislabelled rows, not rule errors -- see *The answer
  key was wrong* below.
- `clauses.py` -- **210 rows, 210 right (1.000)**. The phrase carried two parameters ("shorter
  cooldowns and heavier fireballs") and the model binds one adjective while losing the other; the
  rule reads both, or refuses.

Overlap between the two rules: **0 rows.** Each fires only on its own evidence -- an explicit
exclusion marker (`without`, `except`, `all ... but`, a leading `no`) for the first, a complete clause
reading for the second -- and both return `None` rather than guess, so the model keeps everything
else. Measured false fires outside the families each rule targets: **0**.

The keyword row is the floor, and it is also what the game applies when the service is unreachable.
See the `X-Intent-Source` section below for how to tell which of those happened.

### Each answer names the layer that produced it (`X-Intent-Source`)

Three layers answer `/rules` -- `exclusion.apply_exclusion`, `clauses.apply_clauses`, then the
checkpoint -- and until P31 the game could not tell them apart: every path logged
`Rules applied (intent model)`. With 353 of 588 live rows going through a rule, that made a bad
loadout very hard to attribute.

The answering layer now travels in a response **header**, not a body field:

| `X-Intent-Source` | meaning |
|---|---|
| `no-catalogue` | request carried no token names; `{"tokens": []}` |
| `exclusion` | `exclusion.py` answered (every bucket `default`) |
| `clauses` | `clauses.py` answered (two-clause or single-clause) |
| `model` | the P39 checkpoint answered |

The body had to stay byte-identical. `WishCraftPlayerController.cpp:204` uses `Root->Values.Num()
== 1` to recognise the empty-list abstain sentinel, and `LoadoutValidator.cpp:144` rejects the whole
response when the root holds more than one field. A `source` key in the payload would therefore
have turned every successful rule application into *"The model returned an invalid rules response."*

In game the line now reads `Rules applied (intent model: clauses).` Reproduce it with
`.venv\Scripts\python.exe Saved\verify_intent_source.py`, which prints the header per phrase.

One consequence worth knowing before you go looking: **`no-catalogue` never reaches the game log.**
`serve.py` returns `{"tokens": []}` with that header, and the controller's empty-list test fires
before it reads the header, so the in-game line says
`Rules applied (keyword fallback: model abstained).` `no-catalogue` is visible from the Python probe
only. The same log slot also carries `keyword fallback: model service unavailable` and
`keyword fallback: model request could not start` -- the three ways the box can be missing while
every loadout still applies and nothing else looks wrong.

**The header name is case-sensitive at the reader, and that is the trap that actually bit.** UE
caches response headers in a `TMap<FString, FString>`, and `FHttpResponseCommon::GetHeader`
(`HttpResponseCommon.cpp:76`) does a plain `Headers.Find(HeaderName)` -- no case folding.
uvicorn/Starlette emit names lowercased, so the wire carries `x-intent-source`. A client that asks
only for `X-Intent-Source` gets an empty string with no error and no warning, which is
indistinguishable from a service that never set the header: the log reads
`Rules applied (intent model).` while the probe prints the layer correctly from the same service.
Read both spellings, or scan `GetAllHeaders()` ignoring case. This was found in PIE, not by the
probe -- the probe was right the whole time.

---

## Where the failures are, measured

Per-family, `test.jsonl`, corpus v3 / checkpoint P39 (confidence 0.50):

| family | rows | keyword exact | model exact | model token-set | model bucket |
|---|---|---|---|---|---|
| `allow_terse` | 156 | 1.000 | 0.763 | 0.782 | 0.997 |
| `category_verb` | 12 | 0.500 | 0.583 | 0.583 | 1.000 |
| `exclude_all_but` | 156 | 0.000 | 0.000 | **0.000** | 0.999 |
| `exclude_without` | 156 | 0.000 | 0.000 | **0.000** | 1.000 |
| `only_prefix` | 156 | 1.000 | 0.859 | 0.859 | 1.000 |
| `param_multi_agree` | 187 | 0.000 | 0.166 | 0.775 | **0.609** |
| `param_single_superlative` | 187 | 0.000 | 0.385 | 0.968 | **0.760** |
| `request_can_we` | 156 | 1.000 | 0.724 | 0.763 | 0.978 |

The keyword column reading 1.000 on `allow_terse`, `only_prefix` and `request_can_we` is not a
coincidence: those three families select a token set by naming it, and that is exactly what a
substring matcher does. Fixing the answer key did not change it — those rows were always the
baseline's home ground, and the model's job there is to add the parameter that the baseline cannot.

### What corpus v3 changed: a word that was doing two jobs

P39 changed exactly one thing, and it was a naming defect in the generator, not in the model.
`pick_words` chose each ability's *name* from its alias pool, and that pool contains the speed words --
so a row could render as `sneak, faster`, where `faster` is SpeedBoost's **name**, while a different
row rendered `faster fireballs`, where the same word is a **ProjectileSpeed adjective**. Roughly half
the corpus rows containing a speed word meant the first thing and half the second, so the model could
only guess which, and it lost on both sides: 62 of the 587 live misses before the fix were wrong
token sets, and adding a spurious SpeedBoost to `make the projectile speed on ball of fire much lower`
was the visible symptom.

The fix filters `SPEED_ADJECTIVE_WORDS` out of the name pool in `pick_words`, exactly as `_equip_word`
has done since P23. The adjective still appears where it belongs -- in adjective slots, where
`fam_param_speed_contrast` makes the noun decide whether the ability is present at all. SpeedBoost
keeps ten unambiguous names (`speed`, `speedy`, `boost`, `haste`, `hasty`, `sprint`, `running`, `zoom`,
`velocity`, `turbo`).

| live 3-token | P38 / floor 0.80 | P39 / floor 0.80 | P39 / floor 0.50 |
|---|---|---|---|
| wrong token set | 62 | 48 | 42 |
| right set, wrong bucket | 9 | 10 | 10 |
| as played intent exact | 0.889 | 0.913 | **0.922** |
| `param_single_superlative` wrong sets | 9 | **1** | 2 |

The `param_single_superlative` row is the mechanism confirming itself: that family is where the
adjective and the name collided, and it went from 9 wrong to 1.

The floor 0.50 column is P40. The presence floor had been 0.8 since the first checkpoint, on no
argument but convention; `Saved/sweep_floor.py` re-decodes one forward pass at 0.30 through 0.80 and
shows the curve flat from 0.80 down to about 0.50 and only the invented-token count moving below it.
0.50 is the knee, and on the deployed three-token set it is worth +0.009 intent exact on the service
and +0.009 as played. On the six-token stress set it is a wash (token-set 0.611 -> 0.607, intent
0.407 -> 0.408) -- it buys detection of the unseen token and pays for it in false positives, which is
the right trade only because that set is not what ships. **Quote the floor with the candidate set
it was measured on, or the number means nothing.**

### Verified in the game, and the three things only PIE could find (P40)

Every number above is a re-decode. P40 ran the same four layers through the controller and read the log,
which is how two defects that no measurement in this file could see were found.

1. **The first pass proved nothing, because the binary was stale.** Every line read
   `Rules applied (intent model).` -- the "header did not arrive" row.
   `WishCraftPlayerController.cpp` was dated 11/16 2:40 AM and `UnrealEditor-WishCraft.dll` 11/8 11:25 PM;
   the fix existed only in `patch_0.exe`, and **a Live Coding patch never writes the DLL**. After
   `rebuild_and_restart_editor` the suffix resolved for all three layers (`model`, `clauses`,
   `exclusion`) and `banana milkshake` abstained then refused with the loadout untouched. Compare
   timestamps before diagnosing a missing feature.
2. **`exclusion.py` had a hole in one direction.** `give me a fireball without the speed boost` equipped
   Invisibility -- the only token the sentence never names -- because the rule subtracted every *named*
   candidate. `fam_exclude_without` only ever renders `without <excluded>`, so **143/143 was 100% of a
   happy path** and is a corpus-shaped figure, not completeness. The fix splits the candidate list at the
   marker (`_split_at_marker`): names before it are wanted, names after it are excluded, and the old
   complement survives only when nothing is named before it. Nine corpus-shaped rows are byte-identical
   and three player-shaped rows are corrected (`... fireball without the speed boost` -> Fireball,
   `let me vanish without speed` -> Invisibility, `... except invisibility` -> Fireball). Every corpus
   number reproduced exactly afterwards: 143/143, 210/210, 0.939 / 0.922.
3. **The shipped vocabulary was not the trained one.** The DataAsset held 5 / 5 / 6 keywords and the
   catalogue 12 / 13 / 15; `build_catalogue` lets the request override, so the model was being scored
   against a description it never trained on. `Saved/measure_served_keywords.py` over the 588 live rows:
   0.694 / 0.658 / 51 empty from the old asset, 0.693 / 0.660 / 42 from the catalogue, and
   0.693 / 0.660 / 42 once the asset was aligned. A wash on the corpus -- but `vanish` could not resolve,
   so the exclusion layer fell through to a wrong complement. The asset now carries the naming surface.

The floor was A/B'd in the shipped client as well. `Saved/run_service_floor080.bat` pins
`INTENT_CONFIDENCE=0.80` for the experiment only. At 0.50 `nuke stealth sprint` is
`(intent model: model)` -> Fireball, Invisibility; at 0.80 it is
`(keyword fallback: model abstained)` -> **Fireball, Invisibility, SpeedBoost**. Raising the floor does
not make the path safer, it hands the phrase to the keyword parser, which equips every name it can see.

**The naming surface is one list in two files and the request's copy wins.** Nothing in the build kept
them in agreement, so `Saved/check_keyword_parity.py` does: it asserts
`asset keywords == catalogue keywords minus the banned adjectives`, same order, nothing added. The banned
adjectives are `faster` / `quick` / `quicker` / `rapid` / `slow` / `slower`, sanctioned to be **absent**
from the asset (the client parser equips whatever it matches, so `make the fireball faster` must not
equip SpeedBoost) and forbidden to appear. Current result:

```text
PARITY OK -- 3 live tokens match /Game/Sandbox/DA_AbilityTokens, 3 banned adjective(s) correctly absent.
```

### Defect 1, resolved: the head now decides *whether* a parameter moves

`evaluate.py` reports the bucket decision as the two questions the head actually asks,
because a single accuracy figure hides which one is failing. On `test.jsonl`:

| | model | trivial baseline |
|---|---|---|
| moved (did this parameter move at all?) | **0.941** | 0.889 (always `default`) |
| direction (which way?) | **0.709** | 0.469 (always-most-common) |

On `golden.jsonl` it clears both of its constants: moved 0.860 against 0.839, direction
0.667 against 0.467. Direction there fell from 0.867 at P38, but that is 2 of 15 moved labels --
too few to read as a regression, and it is stated here so nobody has to rediscover it.

Both halves now beat their constants, and `moved` doing so is new. Through P24 it read 0.823 against
0.858: the head emitted a non-`default` bucket slightly too readily. The additive budget was blamed
for that (P16a scored 0.841 on `moved` without it and 0.823 with it) and the trade was never
explained. It is explained now, and it was never the budget: **a fifth of the training labels
asserted a bucket on a sentence that printed no intensity word**, so the only way to fit the corpus
was to move parameters that the text did not ask about. Remove those rows and the same architecture
lands above the constant. Nothing about the head changed between P24 and P38.

Two figures in the report do not reconcile, and that is worth knowing rather than papering
over. `bucket_accuracy` is 0.928 while `moved_accuracy` is 0.924; a correct bucket requires a
correct moved decision, so bucket accuracy should not exceed moved accuracy over the same
parameters. They count different denominators — `score()` walks the parameters of the tokens
the model *predicted*, `bucket_head_report` walks the model's own slot list across every
candidate. This is why the guidance elsewhere in this file is to compare `moved` and
`direction`, each of which carries a constant computed on its own rows.

`moved` is 0.924 at a 0.5 decode threshold, and 0.5 is a convention rather than a tuned
value. `Saved/sweep_moved.py` re-decodes the cached probabilities at other thresholds against
the trivial all-`default` baseline. **The table below is corpus v1 / P24 and has not been re-run**;
it is kept because its conclusion — that no threshold rescues the moved decision — was the reason
the threshold was never tuned, and that reasoning still stands on the numbers it was made on:

| tau | test intent | test bucket | test bkt\|tok |
|---|---|---|---|
| 1.01 (all `default`) | **0.239** | **0.858** | **0.865** |
| 0.50 | 0.104 | 0.737 | 0.649 |
| 0.40 | 0.075 | 0.657 | 0.519 |
| 0.30 | 0.022 | 0.531 | 0.308 |
| 0.20 | 0.003 | 0.424 | 0.110 |

Same verdict as P13: **nothing beats the baseline, and every step down makes it worse.** But
read the table for what it is, because the `tau = 0.50` row (0.737) is nowhere near the 0.872
the same checkpoint scores under `evaluate.py`, and that is not the model changing. The two
decoders ask different questions. The sweep moves a parameter whenever
`1 - P(default) >= tau` and then takes the best of the four moved buckets; `evaluate.py` takes
a plain `argmax` over all five, which only moves when a single moved bucket beats `default`.
Those disagree whenever the moved mass is split — a parameter at `P(moved) = 0.55` with four
buckets near 0.14 moves under the sweep and stays `default` under `argmax`. So this table must
never be quoted as the model's accuracy; what it establishes is only that no threshold on the
moved probability rescues that row.

(The sweep also hands the model the checkpoint's five candidate names, while `evaluate.py`
scores all six catalogue rows including held-out `Blink`. That is why its token-set column
reads 0.497 where the headline reads 0.437.)

### Defect 2: negation, answered by a rule rather than by the model

Both `exclude_*` families score **0.000** token-set accuracy, despite
`exclude_everything_except` and `exclude_no_terse` being *training* families.

That number is still true of the checkpoint, and it is no longer true of the shipped path. The
label these families encode is not a metaphor: the sentence names the abilities to exclude and the
answer is the rest of the candidate set. That is set subtraction, and `exclusion.py` does it before
the model is consulted. It reaches **104 of the 131** live rows it fires on (79%), and 104 is also
its ceiling, for a reason that is a defect in the generator rather than in the rule -- see below.
On the live 3-token set it is **104/104, or 1.000, on every row whose text actually determines the
label**. That second reading is the one to judge it on, and `measure_clauses.py` now prints both.

This was attacked directly. The original presence score was a bare dot product,
`(phrase @ token.T) / temperature`, which scores each candidate independently — and
"everything except X" is defined relative to the candidate set, so no per-pair score can
express it. The head was replaced with an MLP over `[phrase, candidate, candidate-set mean,
similarity, similarity − best]` so it can rank rather than merely match.

The fix helped adjacent things and **not** this one. `exclude_*` stayed at 0.000 through
P16. Its worst neighbour has since improved for an unrelated reason: `param_multi_agree`
token-set exact now reads 0.781, having been 0.155 on corpus v1, purely because the bucket
labels beside those sentences stopped asserting things the text did not say. The set-context head is
in the graph and `exclusion.py` is what actually answers these phrases in the service.

At runtime the failure used to change shape rather than go away; `exclusion.py` is what closed
it. The confidence floor rejected these outright and returned `{"tokens": []}` -- the *designed*
safe failure, since an empty list sends the caller back to the keyword parser -- and once the
model learned to abstain on purpose, an empty list meant "no ability named" rather than "give
up", so the two meanings had to be told apart by the caller. Neither path ever complemented a
set: `give me everything but invisibility` returned a token set that still contained
Invisibility. The rule answers that in the service, with every bucket at `default`, so the
response cannot invent a parameter change either.

### Defect 2b, resolved: the answer key was wrong, and it was being measured as if it were the model

This section changed more than any other, and it changed because the corpus was **rebuilt** rather than
re-measured. It keeps its place so the history stays readable.

Corpus v1 shipped **918 rows across the four splits** whose label asserted something the sentence could
not express -- 194 of them in `test.jsonl`, and 76 on the live 3-token subset that every headline number
was drawn from. Two mechanisms produced them:

1. **Unexpressed buckets.** `prepare()` had no branch for ten families, so they fell through to
   `return sample_factory(rng, mentioned_count=rng.choice([0, 1]))`. Half of those draws pinned a random
   bucket from `BUCKET_WEIGHTS` (`default` = 0.55, so `P(non-default)` = 0.45, and 0.5 x 0.45 = 0.225)
   onto a parameter whose name the sentence never prints. `fam_exclude_all_but` and
   `fam_exclude_without` render only `s.words[n]` for the *excluded* tokens, so
   `all abilities but the shoot, bubble and sprint` carried `Invisibility Cooldown lower` with neither
   word in the text.
2. **One frame, two buckets.** `_param_single_sentence`'s else-branch emitted
   `make the {param} on {word} lower` for both `lower` and `much_lower`. Inside a single split the
   generator's own de-duplication hid it; across splits -- valid and test both phrase through the curated
   vocabulary -- the identical sentence carried two different answers.

The rule that looked broken was not. `exclusion.py` fired on 131 live rows and was right on 104, and
**every one of its 27 misses was a row from mechanism 1**: it was at 100% of what the labels permitted.
The same false reading applied to the clause rule and to the headline itself -- **0.858 was the ceiling,
not the score**, and 0.756 was being compared against a wall it was never going to pass.

Corpus v2 regenerated the phrase set from the corrected generator:

- the ten bucketless families are pinned to `mentioned_count=0` in `prepare()`, so a phrase that renders
  names and nothing else gets an all-`default` label;
- `much_lower` got its own frame in **both** the curated and the generic branch;
- and a third defect the audit found on the way: `param_multi_together` emitted
  `raise both the {a} and the {b} on {word}` for `higher/higher` *and* `max/max`. It was invisible to the
generator because the two rows differed only in the capital of one alias.

`dataset_audit.py` now runs as a **pre-write gate** inside `generate_dataset.main()`: it exits non-zero
and writes no file if it finds either defect class, so a regeneration cannot reintroduce them. The
measurement's `undetermined` line is a canary against the same definition and should read `0 / 0.000`; if
it ever does not, the answer key has regressed and no model change should be spent until it is back at
zero.

**The rebuild also exposed a real bug in `clauses.py`, which is the part worth reading twice.** With the
collision removed, `much lower` became a phrase the corpus actually says -- and the clause rule was still
answering `lower`, because `GENERIC_DIRECTION` reads the single word and `much` is not in it. 12 live rows.
Corpus v1 could not have revealed this: the phrase did not distinguish the two buckets, so `lower` was the
only honest reading and the rule was "right" by accident. The fix is `GENERIC_MULTIWORD`, checked before
the single-word lexicon and applied to whichever parameter the clause named.

Result on the shipped 3-token set: **0 undetermined rows, ceiling 1.000**, `exclusion.py` 143/143 (1.000,
corpus-shaped),
`clauses.py` 210/210 (1.000), service intent exact **0.912**, as played **0.922**.



### Defect 3: abstention overshoots on two confusable phrases

Abstention is not free. Of 10 held-out distractors, 8 return nothing and 2 do not:
`drop the volume a little` and `set the hud brightness to dark` both resolve to a token. Both
are `abstain_template` shapes — a verb plus a noun that is not an ability — so these are the
failures the design predicts rather than surprises. Meanwhile 6 of 6 genuine control requests
still resolve, so the model is not simply quieter everywhere.

### Defect 4: two clauses, one noun (P27)

The worst family the rule cannot reach is `param_multi_agree`: 69 of 98 live rows missed, bucket
accuracy 0.664. It renders `shorter cooldowns and heavier fireballs`, where the first adjective
belongs to a *different* parameter from the second and both must attach to a noun that arrives last.

Support was counted before any cycle was spent (`Saved/audit_multi_agree_support.py`,
`Saved/audit_adjective_support.py`):

| `(parameter, bucket, adjective)` | training rows |
|---|---|
| `Damage higher` / `harder` | 36 |
| `Damage higher` / `stronger` | 29 |
| `Damage higher` / `heavier` | 16 |
| `Damage lower` / `softer` | 11 |
| `ProjectileSpeed lower` / `slower projectiles` | 6 |

Every pairing the test rows ask for already exists in training (0 starved, 374 non-default clauses),
so this is **not** P24's out-of-surface defect. The obvious lever -- widening `CURATED_INTENSITY` --
is barred: `fam_param_multi_agree` reads it while generating the **test** split, so widening it moves
the frozen split hashes and invalidates every number in P23, P24 and P26. `_bare_adjective` is the
only safe pool (train-only, one caller) and would take the thinnest pairing from 11 to roughly 25,
which is a redistribution inside the training set, not new information.

**No retrain. P24 stays.** The failure is binding across a clause boundary, not vocabulary:
`give the speed boost quicker cooldowns and a bigger multiplier` returns Cooldown `higher` -- inverted
-- on adjectives that are well supported, and `make the fireball strong and fast` keeps one clause and
drops the other at the join. Probe unchanged at 14/19. The remaining lever is structural (how two
clauses are joined before the bucket head sees them), and P25 measured one such variant at -0.064 on
direction. See `Docs/BuildJournal.md` P27 and tutorial PART 8.10.

### Defect 4, resolved: the clause rule (P29, widened in P30)

`clauses.apply_clauses` answers these before the model. It splits on `and` / `,` / `but`, resolves
each clause to one `(parameter, bucket)`, and answers only when the evidence is complete -- otherwise
it returns `None` and the model keeps the phrase.

| | fired | right | precision |
|---|---|---|---|
| `param_multi_agree`, live rows | 108 | 108 | **1.000** |
| `param_single_superlative`, live rows | 102 | 102 | **1.000** |
| all live rows | 210 | 210 | **1.000** |

Shipped live intent exact: 0.585 -> 0.650 (P29) -> 0.756 (P30) -> 0.879 (P38) -> **0.922** (P39 +
the 0.50 floor). Tokens 0.823 -> 0.881 -> 0.894 -> 0.939, buckets 0.886 -> 0.950 -> 0.989 ->
0.994. P29/P30's gain was all rule against unchanged P24 weights; P38's is the corpus rebuild plus
one rule fix; P39's is the corpus name fix below plus a confidence floor that had never been swept.

Three design choices carry it. The ability is identified by **which parameter set the clauses fit**
rather than by a keyword -- `quicker cooldowns and a bigger multiplier` names no ability at all, and
only SpeedBoost has both parameters; when a phrase names several, that same test breaks the tie. An
explicit parameter word wins inside its own clause, so `shorter cooldowns` is Cooldown `lower` even on
Invisibility, which also has Duration. And a phrase with no conjunction at all needs only **one**
resolved parameter, because `_resolve_for` fills every unmentioned parameter with `default` -- which
is exactly what a `param_single_superlative` label asks for.

P30 found that over half the family had been left to the model not because the phrases were hard but
because the rule could not read its own dictionary: `_singular` turned `invisibilities` into
`invisibilitie` (the corpus renders `noun_form`, and `pluralise` makes y-ending names `-ies`);
`BARE["longer"]` had no Cooldown reading, so `longer cooldowns` refused ~20 phrases outright; and
`speed` inside `projectile speed` named SpeedBoost. It also measured and **rejected** a
plausible-looking gate on explicit parameter words -- that gate avoided 6 wrong rows and took 18
right ones with it. See `Docs/BuildJournal.md` P29 and P30, and tutorial PART 8.11 and PART 8.12.

---

## Held-out detection (the vocabulary-agnostic claim)

`Blink` is excluded from training entirely — not its name, not its keywords, not any phrase
mentioning it. It is added back only at evaluation, so the row is the only thing that changed.

The bridge is character trigrams: every word is emitted as `[word_id, tri1..triN]` into 2,048
hashed buckets, so an unseen word still gets a non-degenerate vector.

| | detection rate | false-positive rate on control |
|---|---|---|
| dot-product presence (P13) | 0.260 | 0.129 |
| set-context presence (P13) | 0.517 | 0.215 |
| set-context + abstain (P16) | 0.152 | 0.136 |
| set-context + abstain, corpus v3 (P39) | **0.072** | **0.087** |

The set-context win did not survive P16, and it has not come back. At the P38 floor of 0.8 corpus v3
read 0.041 detection / 0.063 false positives; sweeping the floor to 0.50 (`Saved/sweep_floor.log`)
buys +0.031 detection for +0.024 control false positives, and that is the whole of it. The model
finds a token it has never trained on about one time in fourteen. **Do not quote vocabulary
independence as a measured capability yet** -- what is true is only the structural half (nothing in
`serve.py` or `predict` names an ability), and a seventh ability today would be detected by luck.
This is the largest honest gap in the project.

---

## What is actually trustworthy today

- The **contract**. `POST /rules` with the catalogue in the request body; buckets out;
  empty list below the confidence floor. Verified end-to-end against live uvicorn.
- **Vocabulary independence structurally**: nothing in `serve.py` or `predict` refers to an
  ability by name. A seventh ability is a game-side data change, not a redeploy.
- **Parameter direction.** 0.709 against a 0.469 constant on `test`, 0.667 against 0.467 on
  `golden`.
- **The move decision**: 0.941 against the 0.889 always-`default` constant on `test`, 0.860 against
  0.839 on `golden`. Through P24 this read 0.823 against 0.858 and the advice was to treat a
  non-`default` bucket as a suggestion; that caveat is now retired for the shipped path.
- **Abstention**, as far as it has been probed: 8 of 10 held-out distractors return nothing,
  both phrases that used to resolve to nonsense now return nothing, and 6 of 6 control
  requests still resolve.
- **Negation, through `exclusion.py`.** 143 of the 143 live exclusion rows it fires on (all
  corpus-shaped; the rule also had a one-directional hole until P40 split on the marker, see *Verified in
  the game*), and 1.000 on
the six-token set as well, with zero false fires outside those families. It read 104 of 131 on corpus
v1, where all 27 misses were rows with a label the text could not express.
- **Two-clause and one-clause phrases, through `clauses.py`.** 210 of the 588 live rows fire on it
and all 210 are right (1.000): every row of `param_multi_agree` (108/108) and every row of
`param_single_superlative` (102/102), with zero overlap with the exclusion rule. Verified in game:
`maximum projectile speed and longer cooldowns fireballs` resolves Damage `default` 20 /
ProjectileSpeed `max` 5000 / Cooldown `higher` 3.0, a phrase P29 refused outright, and
`punchy but slow fireballs` resolves Damage `higher` 40 / ProjectileSpeed `lower` 1250 / Cooldown
`default` 1.5 where P28 produced ProjectileSpeed `default` / Cooldown `higher` 3.0.
- **Attribution, through `X-Intent-Source`.** Every response names the layer that produced it
  (`no-catalogue` / `exclusion` / `clauses` / `model`), so the game's log line reads
  `Rules applied (intent model: clauses).` The body is unchanged -- see the header section above for
  the two one-field guards that made a payload field impossible. Verified through the player-facing
  path as well as the console: typing into `WBP_RuleInput` and pressing Send produces the same line
  in the log and shows it in the panel's own status text.

---

## Files

| File | Role |
|---|---|
| `catalogue.json` | Training vocabulary, 6 tokens (3 live, 3 `planned`), `held_out_token: Blink` |
| `catalogue_live.json` | Verbatim export of the 3 real tokens from `/Game/Sandbox/DA_AbilityTokens` |
| `schema.py` | `BUCKETS`, `bucket_to_value`, `validate_selection`, `intent_to_json` |
| `generate_dataset.py` | 24 template families (2 of them abstain, 1 equip-only), family-weighted allocation, `audit_split` self-check, and a blocking call into `dataset_audit` before anything is written |
| `dataset_audit.py` | The two defect classes a sentence cannot express: a bucketless family claiming a bucket, and one surface form carrying two labels. Stdlib only, so the generator can import it without torch. Runs standalone, and inside `generate_dataset.main()` as a pre-write gate |
| `train.jsonl` / `valid.jsonl` / `test.jsonl` / `test_heldout.jsonl` | 7,823 / 600 / 1,166 / 293 rows (**corpus v3**) |
| `golden.jsonl` | 20 hand-authored awkward phrases, never generated |
| `train.py` | Two-tower encoder, trigram tokenisation, 1,478,919 params, factorised bucket head. `--no-pooled-phrase` withholds the parameter-agnostic phrase from the bucket trunk — measured as P25 and rejected (it bound parameters correctly but lost direction precision); see the journal |
| `model.pt` | Current checkpoint (**P39**, best epoch 16 of 30, MD5 `8866045eb4ad4014523a7437d6d51b6a`). P24's `3eb0c428…` is backed up at `../../Saved/model_p24_backup.pt` for comparison only. P38 (`541a6250…`) was overwritten by the P39 retrain and can only be reproduced by reverting the `pick_words` filter |
| `evaluate.py` | Keyword-baseline port, per-family table, `bucket_head_report` splits the bucket head into moved and direction |
| `serve.py` | FastAPI `POST /rules`; runs the exclusion and clause rules before the model, and names the answering layer in the `X-Intent-Source` response header |
| `exclusion.py` | Deterministic complement for `without` / `except` / `all ... but` / leading `no`; returns token names with every bucket `default`, or `None` to leave the phrase to the model |
| `clauses.py` | Deterministic clause rule for `and` / `,` / `but`, and for single-clause phrases that name one ability and one parameter; answers only when the evidence is complete, else `None` |
| `eval_live.py` | Scores the 3 shipped tokens, reporting the checkpoint alone and the shipped exclusion+clauses+model path as separate rows |
| `measure_clauses.py` | Fire count, precision, per-family split and every wrong row for the clause rule alone |
| `../../Saved/check_keyword_parity.py` | Editor-side guard between `catalogue.json` and `/Game/Sandbox/DA_AbilityTokens`: asserts `asset == catalogue - banned speed adjectives`, same order, nothing added. Run it after touching either list |
| `eval_report.json` | Machine-readable output of the last evaluation |
| `../../Saved/verify_intent_source.py` | Read-only probe: posts four phrases and prints the `X-Intent-Source` header next to the returned token names |

## Reproduce

```
.venv\Scripts\python.exe Tools\intent_model\generate_dataset.py --seed 20261108
.venv\Scripts\python.exe Tools\intent_model\dataset_audit.py
.venv\Scripts\python.exe Tools\intent_model\train.py
.venv\Scripts\python.exe Tools\intent_model\evaluate.py
.venv\Scripts\python.exe Tools\intent_model\measure_clauses.py
.venv\Scripts\python.exe -m uvicorn serve:app --app-dir Tools/intent_model --port 8077
```

The first command refuses to write anything if the audit fails, so the second is a check on the corpus
that is already on disk rather than a substitute for the gate. `--seed 20261108` is the default and is
what corpus v3 was built with; `train.py` at its defaults (30 epochs, patience 100) reaches P39's
weights at best epoch 16 on CPU in about 30 minutes.

Two commands outside that block are the ones that decided P39 and P40, and neither is part of a
normal rebuild:

```
.venv\Scripts\python.exe Saved\sweep_floor.py    # re-decodes one pass at every confidence floor
```

`Saved/sweep_floor.py` is the twenty-second experiment that would have saved an hour of retraining:
any time a threshold is suspected, sweep it against its own constant before touching the corpus.
`Saved\measure_p39_floor080.log` holds the same checkpoint measured at the retired 0.8 floor, so the
floor's contribution can be separated from the corpus's.

`valid.jsonl` is **not** a good model-selection signal: it contains only `only_verb`
(everything present, all-default) and `param_single_reduce`, so a model that says "yes, all
present, all default" scores well on it. Selection uses `exact_match` there with
`--patience 4`; interpret `valid` numbers with that in mind.

`param_single_comparative` / `_max` / `_superlative` / `_reduce` are **historical names**: they
identify which split a family belongs to, not which bucket it draws, because every one of them draws
all four buckets. So the per-epoch `dir` column is measured on a split that does contain movement, and
on corpus v3 it climbs to about 0.54 by epoch 30 — not the ~0.06 it read when those families were
bucket-pinned. **Do not** use it to judge the direction head anyway: it is `valid.jsonl`, two families
and 600 rows. Use `evaluate.py`'s `bucket_head_report`, which runs on `test.jsonl` and `golden.jsonl`.
That column cost one wrong diagnosis already (a re-weighting scheme was dropped on the strength of it,
before the split artefact was noticed).
