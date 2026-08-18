# Groundedness Contract

This document defines the first, non-destructive groundedness check. It is an
offline evaluation contract, not a claim that every generated answer is proven
to be factually complete.

## Stable source identifiers

- Each retrieved chunk is assigned a stable `S<n>` identifier in ranked order
  for that answer (`S1`, `S2`, ...).
- The identifier is stable only for the answer event. It must not be reused as
  a document ID or treated as an ACL boundary.
- The source payload keeps the original document name, chunk ID, and score next
  to the display identifier so a reviewer can reproduce the mapping.

## Response contract

- The generation prompt may request citations in the form `[S1]` or `[S1,S2]`.
- A citation is valid only when every referenced ID exists in the retrieved
  source list for that answer.
- Citation syntax is metadata about support; it is not a proof of entailment.
- Missing or unknown citations are reported as `unsupported_claim` in offline
  evaluation. The already-streamed answer is never deleted or rewritten.

## Offline evaluation

The initial evaluator should use a manually reviewed golden set and record:

- whether the answer contains at least one valid source ID;
- whether every cited ID exists;
- whether reviewers judge each material claim as supported, contradicted, or
  unsupported;
- the model, retrieval configuration, source snapshot, and case-file hash.

Do not add an NLI model or automatic sentence deletion until this review shows
that citation-presence checks are insufficient. A retrieval hit alone does not
establish answer groundedness.
