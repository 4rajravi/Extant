"""The embedding text. Declared here, embedded in Phase 2.

A paragraph read alone loses its entity scope and its chapter context, so Phase 2 embeds a
composed string rather than the bare body:

    <document title>
    <heading path>              Chapter IV > Reporting > 4.3A
    Applies to: <entity labels>
    <paragraph body>

The result is stored as ``chunks.embed_text`` and is only ever embedded. Quotes,
groundedness checks and the ``fts`` lexical index all use ``chunks.body`` instead; see
``extant.core.models``.

The string is hashed into ``chunks.content_hash`` and keys ``public.vec_cache``, so it must
be deterministic: pass entity labels in a stable order (the order of the chunk's
``entity_codes``), or an unchanged paragraph will needlessly miss the cache.
"""

from __future__ import annotations

from collections.abc import Sequence

from extant.core.models import Document, Paragraph


def compose_embed_text(
    document: Document,
    paragraph: Paragraph,
    entity_labels: Sequence[str],
    *,
    body: str | None = None,
) -> str:
    """Compose the string Phase 2 embeds for one paragraph. A paragraph with no heading
    path omits that line rather than embedding an empty or 'None' line. ``body`` is the
    text under the header: the paragraph's own body by default, or one piece of it when the
    chunker splits an oversized paragraph."""
    if paragraph.doc_id != document.doc_id:
        raise ValueError(f"{paragraph.para_id!r} is not a paragraph of {document.doc_id!r}")
    if not entity_labels:
        raise ValueError("a chunk applies to at least one entity type")
    if any(not label.strip() or "\n" in label for label in entity_labels):
        raise ValueError("entity labels must be non-empty single-line strings")
    if len(set(entity_labels)) != len(entity_labels):
        raise ValueError("entity labels contain duplicates")

    lines = [document.title]
    if paragraph.heading_path:
        lines.append(paragraph.heading_path)
    lines.append(f"Applies to: {', '.join(entity_labels)}")
    lines.append(paragraph.body if body is None else body)
    return "\n".join(lines)
