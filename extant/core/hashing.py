"""Content hashing.

Every ``content_hash`` and ``source_sha256`` in the schema is a lowercase hex SHA-256.
Text is hashed as its exact UTF-8 bytes with no normalisation: normalising whitespace or
Unicode is the parser's job, done before a body is stored, so the hash always describes
exactly the text a citation will be checked against.
"""

import hashlib


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_hex_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
