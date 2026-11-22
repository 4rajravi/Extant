-- Index build schema, version 0002: locate paragraphs without page numbers.
--
-- Decided 19 November 2026: the RBI corpus is read from the HTML direction pages, which have
-- no page numbers. A paragraph is now located by a page number, a source anchor, or both, and
-- every paragraph must have at least one. A source anchor is '#<element id>' (the page's own
-- id, such as '#F9') or 'block:<index>' (the block's position in the extracted page); the page
-- it resolves in is the one whose bytes hash to documents.source_sha256. The grammar lives in
-- extant/core/identity.py.
--
-- Like every numbered file this only shapes new builds; nothing is migrated in place.
-- Placeholders in braces are filled with psycopg.sql.Identifier, so a literal brace anywhere in
-- this file must be written doubled.

SET LOCAL search_path TO {schema}, public;

ALTER TABLE paragraphs ALTER COLUMN source_page DROP NOT NULL;
ALTER TABLE paragraphs ADD COLUMN source_anchor text;
ALTER TABLE paragraphs ADD CONSTRAINT paragraphs_located
  CHECK (source_page IS NOT NULL OR source_anchor IS NOT NULL);

-- Label provenance has the same problem: an annex row read from HTML has no page.
ALTER TABLE concordance ADD COLUMN evidence_anchor text;
