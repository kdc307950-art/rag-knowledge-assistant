# Retrieval Evaluation

`scripts/eval_retrieval.py` is an explicit offline regression command. It
calls `retrieve_context()` directly and never invokes query rewriting or the
LLM. Do not add the recovered Chroma text to Git: the snapshot contains only a
reconstructed source and its original encoding is not reliable.

## Case Schema

Create `eval/retrieval_cases.jsonl` with one object per line. Use schema v2 for
new cases so a single large source can be evaluated at evidence level:

```json
{"schema_version":2,"case_id":"policy-001","query":"...","tags":["proper_noun"],"expected_sources":["policy.txt"],"expected_evidence":[{"source":"policy.txt","chapter":"报销"}],"expected_refusal":false}
```

Tags are corpus-defined metadata. The evaluator accepts stable ASCII
identifiers such as `HR`, `finance`, `proper_noun`, `sensitive`, and
`expected_refusal` (letters, digits, `_`, and `-` only), preserves them in the
report, and does not use them as hidden scoring logic. A history-followup case
may keep a `history` field for documentation, but this pure-retrieval
evaluator ignores it by design; query rewriting must be evaluated separately.

Refusal cases set `expected_refusal: true` and leave `expected_sources` empty.
Answerable v2 cases list one or more exact source names and manually reviewed
`expected_evidence` anchors (`parent_id`, `chapter`, or `paragraph`). Source
Recall is only a coarse document-level signal; evidence Recall/MRR is the
primary metric when the knowledge base contains one large source document. The
set must be manually reviewed against the real source files; synthetic or
guessed labels are not a valid baseline. v1 remains readable for migration but
does not produce evidence metrics.

When multiple active documents contain the same policy, ordinary factual cases
should name one canonical source only. Require all sources only for an explicit
multi-document comparison case; otherwise duplicate documents distort recall.

## Source Policies

Schema v2 may set `source_policy` to `authoritative_only`, `all_required`, or
`any_equivalent`. The last form also requires `source_groups`, where one hit in
each group is sufficient. Reports retain the legacy `recall_at_k` fields and
add `authoritative_recall_at_k`, `multi_document_coverage_at_k`, and
`any_equivalent_hit_rate_at_k`. The active handbook pair currently uses
`any_equivalent` because publication and effective-date evidence is not present
in either source; this is an interim evaluation policy, not an authority claim.

Generate a neutral authoring template after loading a new corpus:

```powershell
uv run python scripts/create_golden_set_template.py `
  --output eval/retrieval_cases.jsonl `
  --count 20
```

The template covers factual, proper-noun, multi-document, history-follow-up,
and refusal cases, but it is deliberately marked `review_status=unreviewed`.
The evaluator rejects it until every case has been checked against the source
document and its metadata, then changed to `approved`.

## Run

```powershell
uv run python scripts/eval_retrieval.py `
  --cases eval/retrieval_cases.jsonl `
  --output eval/results/retrieval-<date>.json `
  --baseline eval/results/retrieval-previous.json
```

The report records the case-file hash, retrieval/model configuration, the
`document_governance_sha256` policy snapshot, and a manifest corpus snapshot
with generation, active revision, content hash, and chunk count for every
active source. `--baseline` refuses comparison with exit code `3` when the
prior report lacks that fingerprint or its corpus differs.
It also reports `evaluation_error_rate` and `busy_rate`; a high Recall/MRR with
non-zero evaluation errors is not a valid clean baseline.

To inspect real anchors before manual annotation:

```powershell
uv run python scripts/inspect_kb_metadata.py `
  --source "三体全文.txt" `
  --contains "红岸工程" `
  --limit 10
```

The command prints metadata and a short excerpt only. It does not create or
approve a case file.

## Feedback Candidates

`scripts/export_feedback_cases.py` exports only admin-accepted feedback to
`eval/dev/`. The output is Git-ignored and contains decrypted question/answer
text, so it must be treated as sensitive. Each record is marked
`manual_label_required=true`; reviewers must verify sources and add evidence
anchors before creating an approved retrieval case. The script refuses to
write to `eval/holdout/`, which remains manual-only.

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
