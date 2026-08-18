# Retrieval Evaluation

`scripts/eval_retrieval.py` is an explicit offline regression command. It
calls `retrieve_context()` directly and never invokes query rewriting or the
LLM. Do not add the recovered Chroma text to Git: the snapshot contains only a
reconstructed source and its original encoding is not reliable.

## Case Schema

Create `eval/retrieval_cases.jsonl` with one object per line:

```json
{"schema_version":1,"case_id":"policy-001","query":"...","tags":["proper_noun"],"expected_sources":["policy.txt"],"expected_refusal":false}
```

Allowed tags are `no_answer`, `multi_document`, `proper_noun`,
`history_followup`, and `expected_refusal`. A history-followup case may keep a
`history` field for documentation, but this pure-retrieval evaluator ignores
it by design; query rewriting must be evaluated separately.

Refusal cases set `expected_refusal: true` and leave `expected_sources` empty.
Answerable cases list one or more exact source names from the document
metadata. The set must be manually reviewed against the real source files;
synthetic or guessed labels are not a valid baseline.

## Run

```powershell
uv run python scripts/eval_retrieval.py `
  --cases eval/retrieval_cases.jsonl `
  --output eval/results/retrieval-<date>.json
```

The report records the case-file hash and retrieval/model configuration so a
future result cannot be compared silently across index generations.
It also reports `evaluation_error_rate` and `busy_rate`; a high Recall/MRR with
non-zero evaluation errors is not a valid clean baseline.

## Groundedness Review

`scripts/eval_groundedness.py` evaluates already-generated answers. Each JSONL
case contains `answer`, the `source_ids` exposed for that answer, and one or
more human-reviewed `claims` with verdict `supported`, `contradicted`,
`unsupported`, or `not_reviewed`. It reports source-ID integrity separately
from human support; a valid `[S1]` is not proof that a claim follows from S1.

```powershell
uv run python scripts/eval_groundedness.py `
  --cases eval/groundedness_cases.jsonl `
  --output eval/results/groundedness-<date>.json
```
