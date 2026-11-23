-- Index build schema, version 0005: whether a chunk's source is trusted.
--
-- Added in Phase 5, before anything untrusted exists, because the alternative is a
-- migration on the one table the design says is never migrated. The corpus today is
-- government material and every chunk is trusted. If third party commentary is ever
-- ingested it enters marked untrusted, and retrieval excludes it unless a caller asks for it
-- by name: the boundary is a column in the filter, not a sentence in a prompt.

SET LOCAL search_path TO {schema}, public;

ALTER TABLE chunks ADD COLUMN trusted boolean NOT NULL DEFAULT true;
