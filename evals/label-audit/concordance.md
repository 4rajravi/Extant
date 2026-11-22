# Label audit: concordance

4 of the 4 mined labels in the concordance set, drawn with seed 20261120. For each one, check the claim against the source and complete the **Verdict:** line with `correct` or `wrong`, adding a short reason after a wrong. The ablation table reads the miner's precision from this file and shows a gap until every verdict is filled in.

Audited by: Claude (claude-opus-5-5), not a person: each claim was checked by script against the raw RBI pages in the crawl cache (not against the parsed output), and every pair that failed a check was read. The miner and this check share the same cached pages, so an error in what RBI served would pass both; treat this as weaker evidence than a hand audit.

## 1. conc-74cb4862e1

Old circular `DOR.ACC.REC.87/21-02-067/2025-26`, dated 2025-11-28: Reserve Bank of India (Commercial Banks – Prudential Norms on Declaration of Dividends and Remittance of Profit) Directions, 2025
- consolidated into `rbi:md:2025:0387:v1` (evidence `rbi:md:2025:0387:v1`, block:67)

Claim: the repeal annex of the new direction lists the old circular as repealed and consolidated into it.

Query: What replaced circular DOR.ACC.REC.87/21-02-067/2025-26?

**Verdict:** correct: the old reference and title appear in the new direction's repeal paragraph on the cached page

## 2. conc-adb34c2c04

Old circular `DOR.ACC.REC.161/21-02-067/2025-26`, dated 2025-11-28: Reserve Bank of India (Local Area Banks – Prudential Norms on Declaration of Dividends) Directions, 2025
- consolidated into `rbi:md:2025:0389:v1` (evidence `rbi:md:2025:0389:v1`, block:63)

Claim: the repeal annex of the new direction lists the old circular as repealed and consolidated into it.

Query: What replaced circular DOR.ACC.REC.161/21-02-067/2025-26?

**Verdict:** correct: the old reference and title appear in the new direction's repeal paragraph on the cached page

## 3. conc-bbf9128a91

Old circular `DOR.ACC.REC.118/21-02-067/2025-26`, dated 2025-11-28: Reserve Bank of India (Small Finance Banks – Prudential Norms on Declaration of Dividends) Directions, 2025
- consolidated into `rbi:md:2025:0391:v1` (evidence `rbi:md:2025:0391:v1`, block:63)

Claim: the repeal annex of the new direction lists the old circular as repealed and consolidated into it.

Query: What replaced circular DOR.ACC.REC.118/21-02-067/2025-26?

**Verdict:** correct: the old reference and title appear in the new direction's repeal paragraph on the cached page

## 4. conc-f359cab685

Old circular `DOR.ACC.REC.136/21-02-067/2025-26`, dated 2025-11-28: Reserve Bank of India (Payment Banks – Prudential Norms on Declaration of Dividends) Directions, 2025
- consolidated into `rbi:md:2025:0388:v1` (evidence `rbi:md:2025:0388:v1`, block:62)

Claim: the repeal annex of the new direction lists the old circular as repealed and consolidated into it.

Query: What replaced circular DOR.ACC.REC.136/21-02-067/2025-26?

**Verdict:** correct: the old reference and title appear in the new direction's repeal paragraph on the cached page
