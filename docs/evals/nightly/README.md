# Nightly run records

One JSON file per nightly run, named by date. Committed, not gitignored.

**These are the regression baseline, and they are not an optimisation.**
`evals/regression.py` reads the previous run from Langfuse first, because
Langfuse sees runs this checkout never did. But Langfuse's free tier retains
data for **30 days**, so after a quiet month the comparison would silently
have no baseline: the nightly would succeed, report no regression, and be
comparing against nothing at all.

These files are what makes the comparison survive that. A run with no baseline
is a green run that measured nothing, and the failure is invisible precisely
when nobody is watching.

Each file is the same shape `python -m evals.gate --out` writes, so the gate,
the committed baselines in `docs/evals/`, and the nightly all describe a run
identically.
