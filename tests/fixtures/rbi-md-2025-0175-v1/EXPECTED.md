# Expected parse: rbi:md:2025:0175:v1

Reserve Bank of India (Small Finance Banks – Licensing) Guidelines, 2025

- Why this fixture: long, with an annex printed in the page
- Source: https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=13135
- Fetched 2026-11-19, source_sha256 `d6be7b22d78243321dfd985c604f9fd1cd16d48a682d8aca118806b7d0f8e0f7`
- Files here: `source.html` (the page as fetched) and `page.txt` (its blocks, numbered)

**For a person to fill in.** Read the source page or `page.txt`, never a program's output, and
replace every TODO_HUMAN below. The parser gate test (`tests/unit/test_parser_gate.py`) reports
an expected failure while any TODO_HUMAN remains, and afterwards checks the parser against
exactly these values.

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

Chapter headings seen (2):
- Chapter-I ‘On tap’ Licensing of Small Finance Banks in the Private Sector
- Chapter-II Voluntary Transition of Urban Co-operative Banks into Small Finance Bank

Annex headings seen (1):
- Annex I

Numbers at the start of a paragraph or heading block (82), as `block: number (kind)`:

- 8: 1 (paragraph)
- 10: 2 (paragraph)
- 11: 3 (paragraph)
- 28: 4 (paragraph)
- 29: 5 (paragraph)
- 32: 6 (paragraph)
- 34: 7 (paragraph)
- 36: 8 (heading)
- 47: 9 (heading)
- 49: 10 (heading)
- 54: 11 (paragraph)
- 55: 12 (paragraph)
- 56: 13 (paragraph)
- 57: 14 (paragraph)
- 68: 15 (paragraph)
- 69: 16 (paragraph)
- 70: 17 (paragraph)
- 72: 18 (paragraph)
- 74: 19 (paragraph)
- 78: 20 (paragraph)
- 80: 21 (paragraph)
- 81: 22 (paragraph)
- 82: 23 (paragraph)
- 83: 24 (paragraph)
- 84: 25 (paragraph)
- 86: 26 (paragraph)
- 88: 27 (paragraph)
- 90: 28 (paragraph)
- 91: 29 (paragraph)
- 92: 30 (paragraph)
- 93: 31 (paragraph)
- 95: 32 (paragraph)
- 96: 33 (paragraph)
- 98: 34 (paragraph)
- 100: 35 (paragraph)
- 102: 36 (paragraph)
- 104: 37 (paragraph)
- 105: 38 (paragraph)
- 106: 39 (paragraph)
- 107: 40 (paragraph)
- 108: 41 (paragraph)
- 109: 42 (paragraph)
- 110: 43 (paragraph)
- 111: 44 (paragraph)
- 113: 45 (paragraph)
- 115: 46 (paragraph)
- 117: 47 (paragraph)
- 118: 48 (paragraph)
- 119: 49 (paragraph)
- 120: 50 (paragraph)
- 121: 51 (paragraph)
- 122: 52 (paragraph)
- 123: 53 (paragraph)
- 124: 54 (paragraph)
- 125: 55 (paragraph)
- 128: 56 (paragraph)
- 132: 57 (paragraph)
- 133: 58 (paragraph)
- 134: 59 (paragraph)
- 136: 60 (paragraph)
- 137: 61 (paragraph)
- 138: 62 (paragraph)
- 139: 63 (paragraph)
- 140: 64 (paragraph)
- 142: 65 (paragraph)
- 143: 66 (heading)
- 147: 67 (paragraph)
- 148: 68 (heading)
- 151: 69 (paragraph)
- 157: 70 (paragraph)
- 158: 71 (paragraph)
- 159: 72 (paragraph)
- 177: 73 (paragraph)
- 182: 74 (heading)
- 190: 1 (paragraph)
- 193: 2 (paragraph)
- 198: 3 (paragraph)
- 203: 4 (paragraph)
- 204: 5 (paragraph)
- 205: 6 (paragraph)
- 207: 7 (paragraph)
- 212: 8 (paragraph)
