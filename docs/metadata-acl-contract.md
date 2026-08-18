# Metadata and ACL Contract

Metadata filters improve retrieval organization, but metadata alone is not a
security boundary. A future ACL implementation must enforce authorization
before retrieval results are exposed to the model or the user.

## Initial metadata vocabulary

- `classification`: one of `policy`, `process`, `benefit`, `technical`, or
  `other`.
- `department`: a short normalized department code, or `general`.
- `visibility`: `private`, `department`, or `all`.
- `source_revision`: the manifest revision that produced the chunk.

The first two fields are business metadata. `visibility` is an access policy
hint and must not be trusted as authorization by itself.

## Defaults and replacement

- Existing documents without new fields are read as `classification=other`,
  `department=general`, and `visibility=all` during migration.
- Re-uploading the same source replaces the active revision's metadata as one
  atomic operation; metadata is not inherited from a previous revision unless
  the upload explicitly supplies it.
- Unknown enum values are rejected at the API boundary rather than silently
  widening access.

## Query protocol

The API may accept optional filters such as `classification` and `department`.
Omitted filters mean “search all authorized documents”, not “bypass ACL”. A
caller cannot supply `visibility` to grant itself access. The effective ACL
filter is computed server-side from the authenticated caller context.

Before changing upload or chat schemas, add contract tests for defaulting,
replacement, unknown values, and the no-filter behavior. Do not implement
multi-user authorization in this phase; this document is the boundary for a
later, explicit security change.
