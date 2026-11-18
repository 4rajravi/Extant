"""Constants shared by the models, the schema tooling and settings.

These mirror fixed facts of the DDL. Changing one here without a matching migration
would let the models accept rows the database rejects, or the other way round.
"""

from typing import Final

# chunks.embedding and public.vec_cache.embedding are halfvec(256).
EMBEDDING_DIM: Final = 256

# Largest finite IEEE 754 half precision value. halfvec rejects anything outside it.
HALFVEC_MAX: Final = 65504.0

# Postgres smallint and integer upper bounds.
SMALLINT_MAX: Final = 32767
INTEGER_MAX: Final = 2_147_483_647

# The read only role serving connects as.
READER_ROLE: Final = "extant_reader"

# pgvector 0.7.0 introduced halfvec.
MIN_PGVECTOR_VERSION: Final = (0, 7, 0)
