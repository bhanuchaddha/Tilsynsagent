# Evals

Measures LLM output fidelity against the golden dataset in `golden/`.

## Modules

| File | Role |
|---|---|
| `cases.py` | Loads the golden dataset and computes each case's expected route. |
| `scorers.py` | Code-based checks against LLM output. |
| `tasks.py` | Runs one case through `assess()`/`summarise()` directly. |
| `sync_dataset.py` | Upserts every case into the Langfuse dataset. Idempotent. |
| `run_baseline.py` | Runs the dataset through the tasks and scores every result. |
| `gate.py` | CI gate: fails a run that regresses below the committed thresholds. |

## Scorers

All code-based, no LLM-as-judge.

| Scorer | What it checks |
|---|---|
| `citation_fidelity` | The source link appears verbatim in the output. |
| `no_invented_numbers` | Every numeral in the output traces to a value in the record. |
| `states_both_values` | Both the before and after value of a changed field appear in the output. |
| `did_not_read_the_document` | The agent works from the register and cites the document, it does not interpret it. |
| `valid_structured_output` | Output matches the strict JSON schema. |

Run-level: `rule_engine_coverage`, `llm_step_error_rate`, `mean_tokens_per_case`, `total_cost_usd`.

## Running

```bash
uv run python -m evals.run_baseline --no-langfuse

uv run python -m evals.run_baseline --no-langfuse --limit 3

uv run python -m evals.sync_dataset
uv run python -m evals.run_baseline
```
