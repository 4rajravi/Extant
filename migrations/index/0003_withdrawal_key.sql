-- Index build schema, version 0003: a stable key for withdrawals.
--
-- Decided 19 November 2026. circular_ref is not unique in RBI's withdrawal list: some
-- notifications are listed once per amendment under the original number and date ("Amended
-- upto 01.10.2002 - ..."), and 154 rows carry no reference at all ("Mail Box Clarification").
-- A withdrawal is identified by its canonical reference (none for those 154), its circular date
-- and its title; withdrawal_key is the sha256 of the three, computed by
-- extant.core.identity.withdrawal_key. circular_ref stays exactly as published; canonical_ref is
-- the join key, NULL when the published cell is not a reference.
--
-- Like every numbered file this only shapes new builds; nothing is migrated in place.
-- Placeholders in braces are filled with psycopg.sql.Identifier, so a literal brace anywhere in
-- this file must be written doubled.

SET LOCAL search_path TO {schema}, public;

ALTER TABLE withdrawals DROP CONSTRAINT withdrawals_pkey;
ALTER TABLE withdrawals ADD COLUMN withdrawal_key char(64) NOT NULL;
ALTER TABLE withdrawals ADD COLUMN canonical_ref text;
ALTER TABLE withdrawals ALTER COLUMN circular_date SET NOT NULL;
ALTER TABLE withdrawals ADD PRIMARY KEY (withdrawal_key);
ALTER TABLE withdrawals ADD CONSTRAINT withdrawals_key_hex
  CHECK (withdrawal_key ~ '^[0-9a-f]{{64}}$');
CREATE INDEX withdrawals_canonical ON withdrawals (canonical_ref);
