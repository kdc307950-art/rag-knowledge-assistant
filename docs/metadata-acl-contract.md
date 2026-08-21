# Metadata and ACL Contract

> Document version: `0.1`
> Applies to application version: `0.1.0`
> Last reviewed: `2026-08-21`

Metadata filters improve retrieval organization. ACL is enforced before vector
or BM25 candidates are returned to the retriever; a caller never supplies its
own effective visibility filter.

## Initial metadata vocabulary

- `classification`: one of `policy`, `process`, `benefit`, `technical`, or
  `other`.
- `department`: a short normalized department code, or `general`.
- `visibility`: `private`, `department`, or `all`.
- `source_revision`: the manifest revision that produced the chunk.

The first two fields are business metadata. `visibility` is evaluated with the
authenticated user context: `all` is visible to every signed-in user,
`department` is visible to the matching department and administrators, and
`private` is visible only to its server-assigned uploader and administrators.

## Defaults and replacement

- Existing documents without new fields are read as `classification=other`,
  `department=general`, and `visibility=all` during migration.
- Re-uploading the same source creates a staging revision. Its source metadata
  and active-revision pointer commit in one manifest transaction, so readers
  see either the old revision or the new revision, never a metadata mismatch.
  Chroma staging data is written before this transaction and is invisible until
  the manifest points to it; this is an atomic visibility switch, not a
  cross-database ACID transaction.
- Upload clients supply `classification`, `department`, and `visibility`; the
  server derives `owner_id` from the authenticated user and never accepts a
  client-supplied owner. `private` uploads without a trusted owner are rejected.
- Unknown enum values are rejected at the API boundary rather than silently
  widening access.

## Query protocol

Omitted query filters mean “search all authorized documents”, not “bypass
ACL”. A caller cannot supply `visibility` to grant itself access. Both vector
and hybrid retrieval derive a revision whitelist from manifest governance plus
the authenticated caller context before candidates reach reranking or the LLM.

Management operations require the `editor` or `admin` role. Documents, counts,
and retrieval are always evaluated against the current caller. Tests cover
defaulting, enum rejection, private ownership, department isolation, manifest
commit, and vector-path filtering; hybrid filtering reuses the same `where`
condition and validates candidates again before returning them.
