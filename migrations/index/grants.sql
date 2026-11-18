-- Serving grants for one build schema.
--
-- Not numbered: the schema tool applies this after every numbered migration in the same
-- transaction, because GRANT SELECT ON ALL TABLES covers only the tables that exist when it
-- runs. A table added by a later migration is therefore granted without touching this
-- file. Placeholders are filled with psycopg.sql.Identifier.

GRANT USAGE ON SCHEMA {schema} TO {reader};
GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO {reader};
