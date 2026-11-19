# Expected parse: rbi:md:2025:0259:v1

Reserve Bank of India (Regional Rural Banks – Interest Rates on Advances) Directions, 2025

- Why this fixture: short and simple
- Source: https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=13047
- Fetched 2026-11-19, source_sha256 `91f387d001c424c61a4ca4e4c325932d6669cdf097bb2ea4b7519f376f861b27`
- Files here: `source.html` (the page as fetched) and `page.txt` (its blocks, numbered)

**For a person to fill in.** Read the source page or `page.txt`, never a program's output, and
replace every TODO_HUMAN below. The parser gate test (`tests/unit/test_parser_gate.py`) fails
while any TODO_HUMAN remains, and afterwards checks the parser against exactly these values.

Rules (decided 19 November 2026): one paragraph per printed paragraph number, taking in its
sub-paragraphs, provisos, lists, tables and the numbered notes under those tables. A paragraph's
anchor is `p<number>` as printed (`p62A`, `p4.1`; a space before an inserted letter is dropped).
A paragraph numbered inside an annex printed in the page is `annex<n>.p<number>`, an annex with no
numbered paragraphs is `annex<n>`, and text before paragraph 1 is `preamble`. Chapter and section
headings, the masthead, the table of contents and footnotes are not paragraphs.

## Counts

- paragraphs: TODO_HUMAN
- chapters: TODO_HUMAN
- annexes: TODO_HUMAN

## Anchors, in document order

One anchor per line, between the fences, for example `preamble`, `p1`, `p2`, `annex1.p1`.

```anchors
TODO_HUMAN
```

## Reading aid (not read by the test)

What I could see by scanning `page.txt` for headings and for numbers at the start of a block.
It is a starting point to check against the page, not an answer: a number here can be a numbered
table note or a footnote, and a paragraph can start without a number in the first block.

Chapter headings seen (3):
- Chapter I - Preliminary
- Chapter II - General guidelines
- Chapter III - Repeal and Other Provisions

Annex headings seen (0):
- none

Numbers at the start of a paragraph or heading block (13), as `block: number (kind)`:

- 7: 1 (paragraph)
- 8: 2 (paragraph)
- 10: 3 (paragraph)
- 12: 4 (paragraph)
- 18: 5 (paragraph)
- 19: 6 (paragraph)
- 21: 7 (paragraph)
- 27: 8 (paragraph)
- 32: 9 (paragraph)
- 35: 10 (paragraph)
- 36: 11 (paragraph)
- 41: 12 (paragraph)
- 43: 13 (paragraph)
