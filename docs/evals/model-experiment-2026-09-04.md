# Model experiment: `gpt-oss-120b` vs `gpt-oss-20b`, 2026-09-04

The question: **how do you say one model is better than another for this
task?** Not by reputation, not by a benchmark table measured on someone else's
data. By running the golden dataset against both, recording both results with
their configuration attached, and publishing where the cheaper one fails.

Reproduces via `uv run python -m evals.experiment`.

| | |
|---|---|
| Candidates | `openai/gpt-oss-120b` (incumbent) vs `openai/gpt-oss-20b` |
| Dataset | 39 cases (34 real + 5 synthetic `not_covered`), 27 of which call a model |
| Prompts | `assess@fallback`, `summarise@fallback` — pinned local text, identical for both models |
| Rule set | `zealand-local-plans-v2`, unchanged |
| Runs | Two independent full comparisons, both reported below |
| Pricing | `config/pricing.toml`, checked by hand against Groq's docs on 2026-09-04 |

**Why these two candidates.** Same provider, same family, same free tier, so
the comparison isolates model size rather than confounding it with vendor,
pricing model, rate limits and API behaviour at once. `llama-3.1-8b-instant`
was considered and rejected on evidence, not preference: it returns 404 on
this key (verified live 2026-09-04), having been decommissioned.

## The result

| Scorer | `gpt-oss-120b` | `gpt-oss-20b` |
|---|---|---|
| `citation_fidelity` | 1.000 / 1.000 | **1.000 / 1.000** |
| `no_invented_numbers` | 1.000 / 1.000 | **1.000 / 1.000** |
| `states_both_values` | 1.000 / 1.000 | **1.000 / 1.000** |
| `did_not_read_the_document` | 1.000 / 1.000 | **1.000 / 1.000** |
| `valid_structured_output` | 0.963 / 1.000 | 1.000 / 0.963 |
| Cost per pass (27 calls) | $0.007725 / $0.007774 | **$0.004015** |

*(run 1 / run 2, where they differ.)*

**The small model matches the large one on every fidelity scorer, across two
independent runs, at 48% of the cost.**

### The one difference is not a difference

`valid_structured_output` shows a single failure in each run — and it is the
**same case, `ZL-023`, on a different model each time**: the 120b in run 1,
the 20b in run 2. Both were transient Groq connection errors, the same class
of flake `baseline-2026-08-22.md` recorded for `ZL-018`. The failure follows
the *case*, not the model, which is exactly why the comparison was run twice.
One run would have supported the false conclusion that the 20b is more
reliable — or, from run 2 alone, that it is less.

This is the argument for experiment-versus-experiment rather than a score in
isolation, made by the data rather than asserted.

## What this does and does not license

**Does:** state, with evidence, that the 20b clears this project's quality bar
on this dataset at half the price. That is a real finding and it is the answer
to "how do you know which model to use".

**Does not:** justify switching the production model today. Three reasons, all
of which are about the evidence being narrower than the claim:

1. **27 LLM calls over 39 cases is a small sample.** The scorers are all at
   1.000, which means the dataset cannot currently distinguish these two
   models at all — the correct reading is "no measurable difference here",
   not "proven equivalent".
2. **The golden dataset is the easy distribution.** It is hand-labelled from
   transitions that already happened and were understood. The cases most
   likely to separate a small model from a large one are the ones nobody has
   labelled yet, which is what the online scorers in `obs/online.py` exist to
   watch.
3. **Cost is not currently a constraint.** Both models are free-tier here. The
   $0.004 saving per pass is real but buys nothing until volume exists, and
   changing a working production model to save nothing is not a trade.

**What would change this:** the online scores accumulating enough live
decisions to compare the two models on traffic the dataset does not contain,
or the volume rising far enough that a 52% cost reduction is worth the
switching risk. Both are measurable, and neither is measurable yet.

**The most useful sentence this produces is a negative one:** *"I did not
switch models, because a cheap model and an expensive model are
indistinguishable on my eval set, and I would rather say that than guess."*
Being able to defend a non-decision with evidence is worth as much as the
decision.

## Method note

`GROQ_MODEL` is read at *import* time by `llm/assess.py` and
`llm/summarise.py`, so `evals/experiment.py` reloads those modules between
candidates and then **asserts that the resolved model is the one it asked
for**, raising rather than proceeding if not. Without that guard, a forgotten
reload measures one model twice and prints the result as two — a plausible
table with no error, which is the worst failure mode a comparison can have.
`tests/test_experiment.py` pins the guard, and pins that every default
candidate has a committed price (an unpriced model silently reports $0.00,
which reads as "free" rather than "unknown" — a bug this experiment's first
run actually hit).
