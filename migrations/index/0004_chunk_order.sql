-- Index build schema, version 0004: the order of chunks within a paragraph and a document.
--
-- Approved 19 November 2026, for Phase 2. The chunker splits a paragraph over roughly 900
-- tokens into pieces that share the parent para_id, so para_id is not unique in chunks and
-- the pieces need their own order: sub_index, 0 for an unsplit paragraph and for the first
-- piece. Citations always resolve to the parent paragraph; sub_index exists for retrieval,
-- never for display.
--
-- ordinal is copied from paragraphs so that neighbourhood expansion (the paragraphs either
-- side of a result, where an exemption usually sits next to its obligation) is one lookup on
-- (doc_id, ordinal) instead of a join back through para_id. The pieces of a split paragraph
-- share their parent's ordinal.
--
-- Like every numbered file this only shapes new builds; nothing is migrated in place.

SET LOCAL search_path TO {schema}, public;

ALTER TABLE chunks ADD COLUMN sub_index smallint NOT NULL DEFAULT 0 CHECK (sub_index >= 0);
ALTER TABLE chunks ADD COLUMN ordinal integer NOT NULL CHECK (ordinal >= 0);
ALTER TABLE chunks ADD CONSTRAINT chunks_para_sub UNIQUE (para_id, sub_index);
CREATE INDEX chunks_doc_ord ON chunks (doc_id, ordinal);
