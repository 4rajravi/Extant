# Expected parse: rbi:md:2025:0382:v1

Reserve Bank of India (Payments Banks – Digital Banking Channels Authorisation) Directions, 2025

- Why this fixture: unusual numbering: numbered headings with decimal sub-numbers
- Source: https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=13101
- Fetched 2026-11-19, source_sha256 `1e01f79d5e963fce044c728fe316620c9200d9171f86b7e00c7c5c490dfc4766`
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

Chapter headings seen (5):
- Chapter I – Preliminary
- Chapter II – Prudential Requirements
- Chapter III – Guidelines on Technological Issues in Digital Banking
- Chapter IV – General Guidelines
- Chapter V – Exemptions, Repeal and Other Provisions

Annex headings seen (0):
- none

Numbers at the start of a paragraph or heading block (30), as `block: number (kind)`:

- 6: 1 (heading)
- 8: 2 (heading)
- 10: 3 (heading)
- 12: 4 (heading)
- 13: 4.1 (paragraph)
- 20: 4.2 (paragraph)
- 22: 5 (heading)
- 24: 6 (heading)
- 25: 6.1 (paragraph)
- 26: 6.2 (paragraph)
- 27: 7 (heading)
- 28: 7.1 (paragraph)
- 37: 7.2 (paragraph)
- 39: 8 (heading)
- 47: 9 (heading)
- 55: 10 (heading)
- 56: 10.1 (paragraph)
- 57: 10.2 (paragraph)
- 58: 10.3 (paragraph)
- 59: 10.4 (paragraph)
- 60: 10.5 (paragraph)
- 61: 10.6 (paragraph)
- 62: 10.7 (paragraph)
- 63: 10.8 (paragraph)
- 65: 11 (heading)
- 67: 12 (heading)
- 68: 12.1 (paragraph)
- 69: 12.2 (paragraph)
- 73: 13 (heading)
- 75: 14 (heading)
