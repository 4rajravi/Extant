# Label audit: temporal

50 of the 158 mined labels in the temporal set, drawn with seed 20261120. For each one, check the claim against the source and complete the **Verdict:** line with `correct` or `wrong`, adding a short reason after a wrong. The ablation table reads the miner's precision from this file and shows a gap until every verdict is filled in.

Audited by: Claude (claude-opus-5-5), not a person: each claim was checked by script against the raw RBI pages in the crawl cache (not against the parsed output), and every pair that failed a check was read. The miner and this check share the same cached pages, so an error in what RBI served would pass both; treat this as weaker evidence than a hand audit.

## 1. temp-05657feb46

Direction 12946, paragraph `p2`.

- Old version `rbi:md:2025:0358:v1`, in force 2025-11-28 to 2026-07-01:
  > 2. Applicability (1) These Directions shall be applicable to following Non-Banking Financial Companies (hereinafter collectively referred to as ‘NBFCs’ and individually as an ‘NBFC’), categorized as NBFC-Middle Layer (NBFC-ML) and above layers: (i) NBFC-D registered with the RBI under the provisions...
- New version `rbi:md:2025:0358:v2`, in force 2026-07-01 to open:
  > 2. Applicability (1) These Directions shall be applicable to following Non-Banking Financial Companies (hereinafter collectively referred to as ‘NBFCs’ and individually as an ‘NBFC’), categorized as NBFC-Middle Layer (NBFC-ML) and above layers: (i) NBFC-D registered with the RBI under the provisions...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-05657feb46-new` as of 2026-07-01, gold `rbi:md:2025:0358:v2#p2`; `temp-05657feb46-old` as of 2026-06-30, gold `rbi:md:2025:0358:v1#p2`

Query: Applicability (1) These Directions shall be applicable to following Non-Banking Financial Companies (hereinafter collectively referred to as ‘NBFCs’ and individually as an ‘NBFC’), categorized as NBFC-Middle Layer (NBFC-ML) and above

**Verdict:** correct: same provision, changed, dates match the version history

## 2. temp-08674dc349

Direction 13162, paragraph `p18`.

- Old version `rbi:md:2025:0148:v5`, in force 2026-10-01 to 2026-10-05:
  > 18. A bank may undertake businesses as permitted under Section 5(b) and Section 6(1) of the Banking Regulation Act, 1949 either departmentally or through a group entity set up as per Section 19 of the Act, subject to the following conditions: (1) The business of banking as defined in section 5(b) of...
- New version `rbi:md:2025:0148:v6`, in force 2026-10-05 to open:
  > 18. A bank may undertake businesses as permitted under Section 5(b) and Section 6(1) of the Banking Regulation Act, 1949 either departmentally or through a group entity set up as per Section 19 of the Act, subject to the following conditions: (1) The business of banking as defined in section 5(b) of...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-08674dc349-new` as of 2026-10-05, gold `rbi:md:2025:0148:v6#p18`; `temp-08674dc349-old` as of 2026-10-04, gold `rbi:md:2025:0148:v5#p18`

Query: A bank may undertake businesses as permitted under Section 5(b) and Section 6(1) of the Banking Regulation Act, 1949 either departmentally or through a group entity set up as per

**Verdict:** correct: same provision, changed, dates match the version history

## 3. temp-0ba42c21cb

Direction 13055, paragraph `p15`.

- Old version `rbi:md:2025:0251:v1`, in force 2025-11-28 to 2026-06-16:
  > 15. The risk weights allotted to each category of balance sheet items and CCF for off-balance sheet items are as under: (1) On-balance sheet items Items of assets | Risk weights (in percentage) I | Balances 1 | Cash and balances with the Reserve Bank | 0 2 | Balances in current account with other ba...
- New version `rbi:md:2025:0251:v2`, in force 2026-06-16 to open:
  > 15. The risk weights allotted to each category of balance sheet items and CCF for off-balance sheet items are as under: (1) On-balance sheet items Items of assets | Risk weights (in percentage) I | Balances 1 | Cash and balances with the Reserve Bank | 0 2 | Balances in current account with other ba...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-0ba42c21cb-new` as of 2026-06-16, gold `rbi:md:2025:0251:v2#p15`; `temp-0ba42c21cb-old` as of 2026-06-15, gold `rbi:md:2025:0251:v1#p15`

Query: The risk weights allotted to each category of balance sheet items and CCF for off-balance sheet items are as under: (1) On-balance sheet items Items of assets | Risk weights

**Verdict:** correct: same provision, changed, dates match the version history

## 4. temp-0bbff70b84

Direction 13121, paragraph `p54`.

- Old version `rbi:md:2025:0188:v3`, in force 2026-07-01 to 2026-10-01:
  > 54. Board shall review the country risk exposures at quarterly intervals. The review shall include progress in establishing internal country rating systems, compliance with the regulatory and the internal limits, results of stress tests and the exit options available to the banks in respect of count...
- New version `rbi:md:2025:0188:v4`, in force 2026-10-01 to open:
  > 54. [The country risk exposures shall be reviewed by the Board or the Risk Management Committee of the Board.] The review shall include progress in establishing internal country rating systems, compliance with the regulatory and the internal limits, results of stress tests and the exit options avail...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-0bbff70b84-new` as of 2026-10-01, gold `rbi:md:2025:0188:v4#p54`; `temp-0bbff70b84-old` as of 2026-09-30, gold `rbi:md:2025:0188:v3#p54`

Query: Board shall review the country risk exposures at quarterly intervals.

**Verdict:** correct: same provision, changed, dates match the version history

## 5. temp-0c7d666f7b

Direction 13002, paragraph `p71`.

- Old version `rbi:md:2025:0302:v1`, in force 2025-11-28 to 2026-04-01:
  > 71. While financing housing schemes in participation with other co-operative banks or commercial banks, it should be ensured that the terms and conditions of such participation arrangements are in conformity with the provisions of the Co-operative Societies Act and the rules framed thereunder.
- New version `rbi:md:2025:0302:v2`, in force 2026-04-01 to open:
  > 71. Act and the rules framed thereunder.While financing housing schemes in participation with other co-operative banks or commercial banks, it should be ensured that the terms and conditions of such participation arrangements are in conformity with the provisions of the Co-operative Societies Act an...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-0c7d666f7b-new` as of 2026-04-01, gold `rbi:md:2025:0302:v2#p71`; `temp-0c7d666f7b-old` as of 2026-03-31, gold `rbi:md:2025:0302:v1#p71`

Query: While financing housing schemes in participation with other co-operative banks or commercial banks, it should be ensured that the terms and conditions of such participation arrangements are in conformity with

**Verdict:** correct: same provision, changed, dates match the version history

## 6. temp-0d7d1deb31

Direction 13140, paragraph `p439`.

- Old version `rbi:md:2025:0170:v1`, in force 2025-11-28 to 2026-04-01:
  > 439. To facilitate interaction between the bank and exporters, the bank should periodically organise Exporters' Meet at centres with concentration of exporters.
- New version `rbi:md:2025:0170:v2`, in force 2026-04-01 to 2026-07-01:
  > 439.To facilitate interaction between the bank and exporters, the bank should periodically organise Exporters' Meet at centres with concentration of exporters.

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-0d7d1deb31-new` as of 2026-04-01, gold `rbi:md:2025:0170:v2#p439`; `temp-0d7d1deb31-old` as of 2026-03-31, gold `rbi:md:2025:0170:v1#p439`

Query: To facilitate interaction between the bank and exporters, the bank should periodically organise Exporters' Meet at centres with concentration of exporters.

**Verdict:** correct: same provision, changed, dates match the version history

## 7. temp-0f0d3a6361

Direction 12939, paragraph `p61`.

- Old version `rbi:md:2025:0365:v2`, in force 2026-03-10 to 2026-04-01:
  > 61. HFCs shall comply with paragraph 13 of Reserve Bank of India (Non-Banking Financial Companies – Credit Risk Management) Directions, 2025.
- New version `rbi:md:2025:0365:v3`, in force 2026-04-01 to 2026-04-15:
  > 61. *****

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-0f0d3a6361-old` as of 2026-03-31, gold `rbi:md:2025:0365:v2#p61`

Query: HFCs shall comply with paragraph 13 of Reserve Bank of India (Non-Banking Financial Companies – Credit Risk Management) Directions, 2025.

**Verdict:** correct: the provision was deleted in the new version (61. *****); the old-side query stands, the new side is not asked

## 8. temp-0fc29875da

Direction 13030, paragraph `p17`.

- Old version `rbi:md:2025:0275:v1`, in force 2025-11-28 to 2026-06-16:
  > 17. RWAs is calculated by multiplying assigned risk-weights to (a) on balance sheet exposure and (b) credit equivalent of off-balance sheet exposure. The credit equivalent of off-balance sheet exposure is calculated by multiplying notional amount of off-balance sheet exposure with credit conversion ...
- New version `rbi:md:2025:0275:v2`, in force 2026-06-16 to open:
  > 17. RWAs is calculated by multiplying assigned risk-weights to (a) on balance sheet exposure and (b) credit equivalent of off-balance sheet exposure. The credit equivalent of off-balance sheet exposure is calculated by multiplying notional amount of off-balance sheet exposure with credit conversion ...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-0fc29875da-new` as of 2026-06-16, gold `rbi:md:2025:0275:v2#p17`; `temp-0fc29875da-old` as of 2026-06-15, gold `rbi:md:2025:0275:v1#p17`

Query: RWAs is calculated by multiplying assigned risk-weights to (a) on balance sheet exposure and (b) credit equivalent of off-balance sheet exposure.

**Verdict:** correct: same provision, changed, dates match the version history

## 9. temp-1bfcbcfe3d

Direction 12944, paragraph `p9`.

- Old version `rbi:md:2025:0360:v1`, in force 2025-11-28 to 2026-07-01:
  > 9. An NBFC eligible to declare dividend as per paragraph 8, may pay dividend, subject to the following: (i) Proposed dividend shall include dividend on both equity shares and compulsorily convertible preference shares eligible for inclusion in Tier 1 Capital / Owned Fund, as applicable. (ii) In case...
- New version `rbi:md:2025:0360:v2`, in force 2026-07-01 to open:
  > 9. An NBFC eligible to declare dividend as per paragraph 8, may pay dividend, subject to the following: (i) Proposed dividend shall include dividend on both equity shares and compulsorily convertible preference shares eligible for inclusion in Tier 1 Capital / Owned Fund, as applicable. (ii) In case...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-1bfcbcfe3d-new` as of 2026-07-01, gold `rbi:md:2025:0360:v2#p9`; `temp-1bfcbcfe3d-old` as of 2026-06-30, gold `rbi:md:2025:0360:v1#p9`

Query: An NBFC eligible to declare dividend as per paragraph 8, may pay dividend, subject to the following: (i) Proposed dividend shall include dividend on both equity shares and compulsorily convertible

**Verdict:** correct: text confirmed on both version pages by eight-word probes

## 10. temp-247c895784

Direction 12929, paragraph `p10`.

- Old version `rbi:md:2025:0375:v1`, in force 2025-11-28 to 2026-07-01:
  > 10. A CI shall adhere to following guidelines on reporting timelines and updation: 1. A CI shall submit data on credit information of its borrowers (including historical data) to all CICs. 2. A CI shall keep the credit information collected / maintained by it, updated regularly on a fortnightly basi...
- New version `rbi:md:2025:0375:v2`, in force 2026-07-01 to open:
  > 10. A CI shall adhere to following guidelines on reporting timelines and updation: (1) A CI shall submit data on credit information of its borrowers (including historical data) to all CICs. (2) A CI shall keep the credit information collected / maintained by it, updated regularly on the following ba...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-247c895784-new` as of 2026-07-01, gold `rbi:md:2025:0375:v2#p10`; `temp-247c895784-old` as of 2026-06-30, gold `rbi:md:2025:0375:v1#p10`

Query: A CI shall adhere to following guidelines on reporting timelines and updation: 1. A CI shall submit data on credit information of its borrowers (including historical data) to all CICs.

**Verdict:** correct: same provision, changed, dates match the version history

## 11. temp-2870b5293d

Direction 12937, paragraph `p11`.

- Old version `rbi:md:2025:0367:v1`, in force 2025-11-28 to 2026-03-10:
  > 11. For the purpose of these Directions, unless the context otherwise requires, the terms herein shall bear the meanings assigned to them below: (1) “adjusted net worth (ANW)” means – (i) the aggregate, as appearing in the last audited balance sheet as at the end of the financial year, of Owned Fund...
- New version `rbi:md:2025:0367:v2`, in force 2026-03-10 to open:
  > 11. For the purpose of these Directions, unless the context otherwise requires, the terms herein shall bear the meanings assigned to them below: (1) “adjusted net worth (ANW)” means – (i) the aggregate, as appearing in the last audited balance sheet as at the end of the financial year, of Owned Fund...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-2870b5293d-new` as of 2026-03-10, gold `rbi:md:2025:0367:v2#p11`; `temp-2870b5293d-old` as of 2026-03-09, gold `rbi:md:2025:0367:v1#p11`

Query: For the purpose of these Directions, unless the context otherwise requires, the terms herein shall bear the meanings assigned to them below: (1) “adjusted net worth (ANW)” means – (i)

**Verdict:** correct: same provision, changed, dates match the version history

## 12. temp-2bcdeb1bdb

Direction 13025, paragraph `p14`.

- Old version `rbi:md:2025:0280:v1`, in force 2025-11-28 to 2026-04-01:
  > 14. A UCB shall be guided by Master Direction – Reserve Bank of India (Filing of Supervisory Returns) Directions – 2024 dated February 27, 2024, as amended from time to time, for submission of return regarding Director-related loans.
- New version `rbi:md:2025:0280:v2`, in force 2026-04-01 to 2026-07-01:
  > 14. *****]

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-2bcdeb1bdb-old` as of 2026-03-31, gold `rbi:md:2025:0280:v1#p14`

Query: A UCB shall be guided by Master Direction – Reserve Bank of India (Filing of Supervisory Returns) Directions – 2024 dated February 27, 2024, as amended from time to time,

**Verdict:** correct: the provision was deleted in the new version (14. *****]); the old-side query stands, the new side is not asked

## 13. temp-2c03298dd1

Direction 13063, paragraph `p123`.

- Old version `rbi:md:2025:0244:v2`, in force 2026-04-01 to 2026-07-01:
  > 123.An LAB shall give wide publicity and provide guidance to deposit account holders, locker hirers and depositors of articles in safe custody on the benefits of the nomination facility. This may include printing compatible messages on cheque book, passbook and other literature reaching the customer...
- New version `rbi:md:2025:0244:v3`, in force 2026-07-01 to 2026-10-01:
  > 123. An LAB shall give wide publicity and provide guidance to deposit account holders, locker hirers and depositors of articles in safe custody on the benefits of the nomination facility. This may include printing compatible messages on cheque book, passbook and other literature reaching the custome...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-2c03298dd1-new` as of 2026-07-01, gold `rbi:md:2025:0244:v3#p123`; `temp-2c03298dd1-old` as of 2026-06-30, gold `rbi:md:2025:0244:v2#p123`

Query: 123.An LAB shall give wide publicity and provide guidance to deposit account holders, locker hirers and depositors of articles in safe custody on the benefits of the nomination facility.

**Verdict:** correct: same provision, changed, dates match the version history

## 14. temp-3544fce253

Direction 13044, paragraph `p29`.

- Old version `rbi:md:2025:0262:v1`, in force 2025-11-28 to 2026-07-01:
  > 29. Where natural calamities impair the repaying capacity of agricultural borrowers for the purposes specified in paragraph 25, a bank may decide on its own as a relief measure conversion of the short-term production loan into a term loan or re-schedulement of the repayment period; and the sanctioni...
- New version `rbi:md:2025:0262:v2`, in force 2026-07-01 to 2026-10-01:
  > 29. [***]

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-3544fce253-old` as of 2026-06-30, gold `rbi:md:2025:0262:v1#p29`

Query: Where natural calamities impair the repaying capacity of agricultural borrowers for the purposes specified in paragraph 25, a bank may decide on its own as a relief measure conversion of

**Verdict:** correct: the provision was deleted in the new version (29. [***]); the old-side query stands, the new side is not asked

## 15. temp-357a17262b

Direction 12973, paragraph `p68`.

- Old version `rbi:md:2025:0331:v1`, in force 2025-11-28 to 2026-07-01:
  > 68. The provisions towards standard assets need not be netted from gross advances but shown separately as 'Contingent Provisions against Standard Assets' under 'Other Liabilities and Provisions Others' in Schedule 5 of the balance sheet.
- New version `rbi:md:2025:0331:v2`, in force 2026-07-01 to 2026-10-01:
  > 68. The provisions towards standard assets need not be netted from gross advances but shown separately as 'Contingent Provisions against Standard Assets' under 'Other Liabilities and Provisions Others' in Schedule 5 of the balance sheet. [B1. Additional specific provisioning in case of resolution pl...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-357a17262b-new` as of 2026-07-01, gold `rbi:md:2025:0331:v2#p68`; `temp-357a17262b-old` as of 2026-06-30, gold `rbi:md:2025:0331:v1#p68`

Query: The provisions towards standard assets need not be netted from gross advances but shown separately as 'Contingent Provisions against Standard Assets' under 'Other Liabilities and Provisions Others' in Schedule 5

**Verdict:** correct: same provision, changed, dates match the version history

## 16. temp-367f1414ca

Direction 13053, paragraph `p62`.

- Old version `rbi:md:2025:0253:v1`, in force 2025-11-28 to 2026-04-01:
  > 62. The Directions shall not apply to the derivative exposures of the bank (wherever permitted under specific regulatory framework issued by RBI), other than the General Conditions as laid down in this Chapter.
- New version `rbi:md:2025:0253:v2`, in force 2026-04-01 to open:
  > 62. The Directions shall not apply to the derivative exposures of the bank(wherever permitted under specific regulatory framework issued by RBI), other than the General Conditions as laid down in this Chapter.

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-367f1414ca-new` as of 2026-04-01, gold `rbi:md:2025:0253:v2#p62`; `temp-367f1414ca-old` as of 2026-03-31, gold `rbi:md:2025:0253:v1#p62`

Query: The Directions shall not apply to the derivative exposures of the bank (wherever permitted under specific regulatory framework issued by RBI), other than the General Conditions as laid down in

**Verdict:** correct: same provision, changed, dates match the version history

## 17. temp-374f1c9b84

Direction 13069, paragraph `p68`.

- Old version `rbi:md:2025:0238:v2`, in force 2026-07-01 to 2026-10-01:
  > 68. A bank shall estimate the riskiness of unhedged position of their borrowers as per the instructions on unhedged foreign currency exposures contained in the Reserve Bank of India (Local Area Banks – Credit Risk Management) Directions, 2025 and make incremental provisions on all their exposures to...
- New version `rbi:md:2025:0238:v3`, in force 2026-10-01 to open:
  > 68. A bank shall estimate the riskiness of unhedged position of their borrowers as per the instructions on unhedged foreign currency exposures contained in the Reserve Bank of India (Local Area Banks – Credit Risk Management) Directions, 2025 and make incremental provisions on all their exposures to...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-374f1c9b84-new` as of 2026-10-01, gold `rbi:md:2025:0238:v3#p68`; `temp-374f1c9b84-old` as of 2026-09-30, gold `rbi:md:2025:0238:v2#p68`

Query: A bank shall estimate the riskiness of unhedged position of their borrowers as per the instructions on unhedged foreign currency exposures contained in the Reserve Bank of India (Local Area

**Verdict:** correct: same provision, changed, dates match the version history

## 18. temp-3a6d7ada20

Direction 13152, paragraph `p94`.

- Old version `rbi:md:2025:0158:v3`, in force 2026-07-01 to 2026-10-01:
  > 94. The bank shall be guided by the Reserve Bank of India (Commercial Banks – Credit Facilities) Directions, 2025.
- New version `rbi:md:2025:0158:v4`, in force 2026-10-01 to open:
  > 94. [*****]

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-3a6d7ada20-old` as of 2026-09-30, gold `rbi:md:2025:0158:v3#p94`

Query: The bank shall be guided by the Reserve Bank of India (Commercial Banks – Credit Facilities) Directions, 2025.

**Verdict:** correct: the provision was deleted in the new version (94. [*****]); the old-side query stands, the new side is not asked

## 19. temp-3f65bacf79

Direction 13146, paragraph `p84`.

- Old version `rbi:md:2025:0164:v2`, in force 2026-01-01 to 2026-07-01:
  > 84. A bank shall estimate the riskiness of unhedged position of their borrowers as per the instructions on unhedged foreign currency exposures contained in the Reserve Bank of India (Commercial Banks – Credit Risk Management) Directions, 2025 and make incremental provisions on all their exposures to...
- New version `rbi:md:2025:0164:v3`, in force 2026-07-01 to 2026-10-01:
  > 84. A bank shall estimate the riskiness of unhedged position of their borrowers as per the instructions on unhedged foreign currency exposures contained in the Reserve Bank of India (Commercial Banks – Credit Risk Management) Directions, 2025 and make incremental provisions on all their exposures to...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-3f65bacf79-new` as of 2026-07-01, gold `rbi:md:2025:0164:v3#p84`; `temp-3f65bacf79-old` as of 2026-06-30, gold `rbi:md:2025:0164:v2#p84`

Query: A bank shall estimate the riskiness of unhedged position of their borrowers as per the instructions on unhedged foreign currency exposures contained in the Reserve Bank of India (Commercial Banks

**Verdict:** correct: same provision, changed, dates match the version history

## 20. temp-438aea53e7

Direction 13157, paragraph `p10`.

- Old version `rbi:md:2025:0153:v3`, in force 2026-08-25 to 2026-10-01:
  > 10. In addition to the conditions laid down in paragraph 7 of these Directions, interest rates on term deposits shall vary only on account of one or more of the following reasons: (1) Tenor of deposits A bank shall have the freedom to determine the maturity / tenor of the deposit subject to the cond...
- New version `rbi:md:2025:0153:v4`, in force 2026-10-01 to open:
  > 10. In addition to the conditions laid down in paragraph 7 of these Directions, interest rates on term deposits shall vary only on account of one or more of the following reasons: (1) Tenor of deposits A bank shall have the freedom to determine the maturity / tenor of the deposit subject to the cond...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-438aea53e7-new` as of 2026-10-01, gold `rbi:md:2025:0153:v4#p10`; `temp-438aea53e7-old` as of 2026-09-30, gold `rbi:md:2025:0153:v3#p10`

Query: In addition to the conditions laid down in paragraph 7 of these Directions, interest rates on term deposits shall vary only on account of one or more of the following

**Verdict:** correct: same provision, changed, dates match the version history

## 21. temp-48fc2a8eed

Direction 12959, paragraph `p9`.

- Old version `rbi:md:2025:0345:v1`, in force 2025-11-28 to 2026-03-10:
  > 9. ‘Owned Fund’ means aggregate of: (i) paid up equity capital; (ii) preference shares which are compulsorily convertible into equity; (iii) free reserves; (iv) balance in share premium account; and (v) capital reserves representing surplus arising out of sale proceeds of asset, excluding reserves c...
- New version `rbi:md:2025:0345:v2`, in force 2026-03-10 to 2026-04-01:
  > 9. ‘Owned Fund’ means aggregate of: (i) paid up equity capital; (ii) preference shares which are compulsorily convertible into equity; (iii) [free reserves, including quarterly profits, Inclusion of quarterly profits shall be subject to the following conditions: (a) The financial statements shall be...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-48fc2a8eed-new` as of 2026-03-10, gold `rbi:md:2025:0345:v2#p9`; `temp-48fc2a8eed-old` as of 2026-03-09, gold `rbi:md:2025:0345:v1#p9`

Query: ‘Owned Fund’ means aggregate of: (i) paid up equity capital;

**Verdict:** correct: same provision, changed, dates match the version history

## 22. temp-5f70474618

Direction 12993, paragraph `p51`.

- Old version `rbi:md:2025:0311:v2`, in force 2026-02-13 to 2026-07-01:
  > 51. Provisioning Pertaining to Fraud Accounts (1) The entire amount due to a bank (irrespective of quantum of security held against such assets), or for which the bank is liable (including in case of deposit accounts), shall be provided for over a period not exceeding four quarters commencing with t...
- New version `rbi:md:2025:0311:v3`, in force 2026-07-01 to 2026-10-01:
  > 51. Provisioning Pertaining to Fraud Accounts (1) The entire amount due to a bank (irrespective of quantum of security held against such assets), or for which the bank is liable (including in case of deposit accounts), shall be provided for over a period not exceeding four quarters commencing with t...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-5f70474618-new` as of 2026-07-01, gold `rbi:md:2025:0311:v3#p51`; `temp-5f70474618-old` as of 2026-06-30, gold `rbi:md:2025:0311:v2#p51`

Query: Provisioning Pertaining to Fraud Accounts (1) The entire amount due to a bank (irrespective of quantum of security held against such assets), or for which the bank is liable (including

**Verdict:** correct: same provision, changed, dates match the version history

## 23. temp-646eca7a84

Direction 13102, paragraph `p7`.

- Old version `rbi:md:2025:0207:v1`, in force 2025-11-28 to 2026-10-01:
  > 7. The Board of a bank shall fulfil the following responsibilities: (1) Banking Outlets i. The Board shall ensure regular off-site and on-site monitoring system of the ‘Banking Outlet’ to ensure proper supervision, ‘uninterrupted service’ and timely addressing of customer grievances. ii. The Board s...
- New version `rbi:md:2025:0207:v2`, in force 2026-10-01 to open:
  > 7. The Board of a bank shall fulfil the following responsibilities: (1) Banking Outlets i. The Board shall ensure regular off-site and on-site monitoring system of the ‘Banking Outlet’ to ensure proper supervision, ‘uninterrupted service’ and timely addressing of customer grievances. ii. The Board o...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-646eca7a84-new` as of 2026-10-01, gold `rbi:md:2025:0207:v2#p7`; `temp-646eca7a84-old` as of 2026-09-30, gold `rbi:md:2025:0207:v1#p7`

Query: The Board of a bank shall fulfil the following responsibilities: (1) Banking Outlets i.

**Verdict:** correct: text confirmed on both version pages by eight-word probes

## 24. temp-6e5b531e45

Direction 13078, paragraph `p92`.

- Old version `rbi:md:2025:0229:v1`, in force 2025-11-28 to 2026-04-01:
  > 92. With the issue of these Directions, the existing Directions, instructions, and guidelines relating to credit facilities for Local Area Banks, stands repealed, as communicated vide notification dated XX, 2025. The Directions, instructions and guidelines already repealed shall continue to remain r...
- New version `rbi:md:2025:0229:v2`, in force 2026-04-01 to open:
  > 92. With the issue of these Directions, the existing Directions, instructions, and guidelines relating to credit facilities for Local Area Banks, stands repealed, as communicated vide notification dated November 28, 2025. The Directions, instructions and guidelines already repealed shall continue to...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-6e5b531e45-new` as of 2026-04-01, gold `rbi:md:2025:0229:v2#p92`; `temp-6e5b531e45-old` as of 2026-03-31, gold `rbi:md:2025:0229:v1#p92`

Query: With the issue of these Directions, the existing Directions, instructions, and guidelines relating to credit facilities for Local Area Banks, stands repealed, as communicated vide notification dated XX, 2025. The

**Verdict:** correct: same provision, changed, dates match the version history

## 25. temp-75451289bd

Direction 13033, paragraph `p18`.

- Old version `rbi:md:2025:0272:v1`, in force 2026-01-01 to 2026-04-27:
  > 18. Notwithstanding the above provisions: 1. Outstanding investment by a UCB, as on July 29, 2025 in an AIF Scheme in which it has fully honoured its commitment, shall be governed by the provisions mentioned in Annex II. 2. In respect of any investment made by a UCB in an AIF Scheme in terms of an e...
- New version `rbi:md:2025:0272:v2`, in force 2026-04-27 to 2026-10-05:
  > 18. [Deleted].

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-75451289bd-old` as of 2026-04-26, gold `rbi:md:2025:0272:v1#p18`

Query: Notwithstanding the above provisions: 1. Outstanding investment by a UCB, as on July 29, 2025 in an AIF Scheme in which it has fully honoured its commitment, shall be governed

**Verdict:** correct: the provision was deleted in the new version (18. [Deleted].); the old-side query stands, the new side is not asked

## 26. temp-76bfb5b9b9

Direction 12957, paragraph `p25`.

- Old version `rbi:md:2025:0347:v1`, in force 2025-11-28 to 2026-02-13:
  > 25. Recognition of NPA (1) Recognition of individual loan assets in the portfolio as Non-Performing Asset (NPA) and consequent provisioning shall be the responsibility of the NBFC as per the Reserve Bank of India (Non-Banking Financial Companies – Income Recognition, Asset Classification and Provisi...
- New version `rbi:md:2025:0347:v2`, in force 2026-02-13 to 2026-04-01:
  > 25. Recognition of NPA (1) [Asset classification of individual loan assets and consequent provisioning requirement shall be in terms of the Reserve Bank of India (Non-Banking Financial Companies – Income Recognition, Asset Classification and Provisioning) Directions, 2025.] (2) The amount of DLG inv...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-76bfb5b9b9-new` as of 2026-02-13, gold `rbi:md:2025:0347:v2#p25`; `temp-76bfb5b9b9-old` as of 2026-02-12, gold `rbi:md:2025:0347:v1#p25`

Query: Recognition of NPA (1) Recognition of individual loan assets in the portfolio as Non-Performing Asset (NPA) and consequent provisioning shall be the responsibility of the NBFC as per the Reserve

**Verdict:** correct: text confirmed on both version pages by eight-word probes

## 27. temp-78da2ffc5e

Direction 13087, paragraph `p56`.

- Old version `rbi:md:2025:0221:v1`, in force 2025-11-28 to 2026-04-01:
  > 56. As specified in paragraph 7(2), a bank’s Board shall formulate a policy for allowing donations, including the purpose for which such donations may be made, keeping in view the following instructions: (1) A bank may make donations during a financial year aggregating up to one per cent of the publ...
- New version `rbi:md:2025:0221:v2`, in force 2026-04-01 to 2026-10-01:
  > 56. As specified in paragraph 7(2), a bank’s Board shall formulate a policy for allowing donations, including the purpose for which such donations may be made, keeping in view the following instructions: (1) A bank may make donations during a financial year aggregating up to one per cent of the publ...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-78da2ffc5e-new` as of 2026-04-01, gold `rbi:md:2025:0221:v2#p56`; `temp-78da2ffc5e-old` as of 2026-03-31, gold `rbi:md:2025:0221:v1#p56`

Query: As specified in paragraph 7(2), a bank’s Board shall formulate a policy for allowing donations, including the purpose for which such donations may be made, keeping in view the following

**Verdict:** correct: same provision, changed, dates match the version history

## 28. temp-8cc5910389

Direction 13068, paragraph `p31Y`.

- Old version `rbi:md:2025:0239:v2`, in force 2026-07-01 to 2026-10-01:
  > 31Y. In case no relief measures are extended, a 'NIL' statement shall be uploaded by the bank.
- New version `rbi:md:2025:0239:v3`, in force 2026-10-01 to open:
  > 31Y. In case no relief measures are extended, a 'NIL' statement shall be uploaded by the bank].

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-8cc5910389-new` as of 2026-10-01, gold `rbi:md:2025:0239:v3#p31Y`; `temp-8cc5910389-old` as of 2026-09-30, gold `rbi:md:2025:0239:v2#p31Y`

Query: 31Y. In case no relief measures are extended, a 'NIL' statement shall be uploaded by the bank.

**Verdict:** correct: same provision, changed, dates match the version history

## 29. temp-9bc1527107

Direction 13081, paragraph `p8B`.

- Old version `rbi:md:2025:0226:v4`, in force 2026-09-11 to 2026-10-09:
  > 8B. Every scheduled bank shall maintain minimum CRR of not less than 90 percent of the required CRR on all days during the reporting fortnight, in such a manner that the average of CRR maintained daily shall not be less than the CRR prescribed by the Reserve Bank.
- New version `rbi:md:2025:0226:v5`, in force 2026-10-09 to open:
  > 8B. Every scheduled bank shall maintain minimum CRR of not less than 90 percent of the required CRR on all days during the reporting fortnight till October 15, 2026, and 99 percent from the fortnight beginning October 16, 2026, in such a manner that the average of CRR maintained daily shall not be l...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-9bc1527107-new` as of 2026-10-09, gold `rbi:md:2025:0226:v5#p8B`; `temp-9bc1527107-old` as of 2026-10-08, gold `rbi:md:2025:0226:v4#p8B`

Query: 8B. Every scheduled bank shall maintain minimum CRR of not less than 90 percent of the required CRR on all days during the reporting fortnight, in such a manner that

**Verdict:** correct: same provision, changed, dates match the version history

## 30. temp-9c473bd7ec

Direction 12959, paragraph `p5`.

- Old version `rbi:md:2025:0345:v2`, in force 2026-03-10 to 2026-04-01:
  > 5. All other expressions unless defined herein shall have the same meaning as have been assigned to them under the Reserve Bank of India Act, 1934 or Companies Act, 2013 and rules / regulations made thereunder, or any statutory modification or re-enactment thereto or as used in commercial parlance, ...
- New version `rbi:md:2025:0345:v3`, in force 2026-04-01 to 2026-06-16:
  > 5. All other expressions unless defined herein shall have the same meaning as have been assigned to them under the Reserve Bank of India Act, 1934 or Companies Act, 2013 and rules / regulations made thereunder, or any statutory modification or re-enactment thereto or as used in commercial parlance, ...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-9c473bd7ec-new` as of 2026-04-01, gold `rbi:md:2025:0345:v3#p5`; `temp-9c473bd7ec-old` as of 2026-03-31, gold `rbi:md:2025:0345:v2#p5`

Query: All other expressions unless defined herein shall have the same meaning as have been assigned to them under the Reserve Bank of India Act, 1934 or Companies Act, 2013 and

**Verdict:** correct: heading differs only cosmetically

## 31. temp-a097fec5c5

Direction 13058, paragraph `p16`.

- Old version `rbi:md:2025:0248:v1`, in force 2026-01-01 to 2026-04-27:
  > 16. As stated in paragraph 2, the provisions of paragraphs 10 to 15 of this Direction shall come into force with effect from January 1, 2026, or from an earlier date as decided by an RRB in line with its internal policy (referred to as the ‘effective date’ for the provisions of paragraphs 10 to 15 a...
- New version `rbi:md:2025:0248:v2`, in force 2026-04-27 to 2026-10-05:
  > 16. [Deleted]

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-a097fec5c5-old` as of 2026-04-26, gold `rbi:md:2025:0248:v1#p16`

Query: As stated in paragraph 2, the provisions of paragraphs 10 to 15 of this Direction shall come into force with effect from January 1, 2026, or from an earlier date

**Verdict:** correct: the provision was deleted in the new version (16. [Deleted]); the old-side query stands, the new side is not asked

## 32. temp-ab5a2ca7f5

Direction 13028, paragraph `p104`.

- Old version `rbi:md:2025:0277:v2`, in force 2026-04-01 to 2026-07-15:
  > 104. UCBs may grant loans to the following categories of borrowers: (1) Individuals and co-operative / group housing societies. (2) Housing boards undertaking housing projects or schemes for economically weaker Chapters (EWS), low income groups (LIG) and middle income groups (MIG). (3) Owners of hou...
- New version `rbi:md:2025:0277:v3`, in force 2026-07-15 to 2026-10-01:
  > 104. UCBs may grant loans to the following categories of borrowers: (1) Individuals and co-operative / group housing societies. (2) Housing boards undertaking housing projects or schemes for economically weaker sections (EWS), low income groups (LIG) and middle income groups (MIG). (3) Owners of hou...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-ab5a2ca7f5-new` as of 2026-07-15, gold `rbi:md:2025:0277:v3#p104`; `temp-ab5a2ca7f5-old` as of 2026-07-14, gold `rbi:md:2025:0277:v2#p104`

Query: UCBs may grant loans to the following categories of borrowers: (1) Individuals and co-operative / group housing societies.

**Verdict:** correct: same provision, changed, dates match the version history

## 33. temp-b5cc8ea9f5

Direction 12970, paragraph `p19`.

- Old version `rbi:md:2025:0334:v1`, in force 2025-11-28 to 2026-04-01:
  > 19. In addition to the detailed schedules to the balance sheet, an AIFI is required to furnish the following information in ‘Notes to Accounts’: (1) Capital adequacy* (Amount in ₹ crores) Sr. No. | Particulars | Current Year | Previous Year i) | Common Equity Tier 1 capital (CET 1) (net of deduction...
- New version `rbi:md:2025:0334:v2`, in force 2026-04-01 to open:
  > 19. In addition to the detailed schedules to the balance sheet, an AIFI is required to furnish the following information in ‘Notes to Accounts’: (1) Capital adequacy* (Amount in ₹ crores) Sr. No. | Particulars | Current Year | Previous Year i) | Common Equity Tier 1 capital (CET 1) (net of deduction...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-b5cc8ea9f5-new` as of 2026-04-01, gold `rbi:md:2025:0334:v2#p19`; `temp-b5cc8ea9f5-old` as of 2026-03-31, gold `rbi:md:2025:0334:v1#p19`

Query: In addition to the detailed schedules to the balance sheet, an AIFI is required to furnish the following information in ‘Notes to Accounts’: (1) Capital adequacy* (Amount in ₹ crores)

**Verdict:** correct: same provision, changed, dates match the version history

## 34. temp-ba406b30ef

Direction 13098, paragraph `p26`.

- Old version `rbi:md:2025:0210:v2`, in force 2025-12-11 to 2026-01-22:
  > 26. SLR assets shall be maintained by PBs as under: (1) Cash, or; (2) Gold, as defined in Section 5(g) of the Banking Regulation Act, 1949 (10 of 1949), valued at a price not exceeding the current market price, or; (3) Unencumbered investment in any of the following instruments [hereinafter referred...
- New version `rbi:md:2025:0210:v3`, in force 2026-01-22 to 2026-10-09:
  > 26. SLR assets shall be maintained by PBs as under: (1) Cash, or; (2) Gold, as defined in Section 5(g) of the Banking Regulation Act, 1949 (10 of 1949), valued at a price not exceeding the current market price, or; (3) Unencumbered investment in any of the following instruments [hereinafter referred...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-ba406b30ef-new` as of 2026-01-22, gold `rbi:md:2025:0210:v3#p26`; `temp-ba406b30ef-old` as of 2026-01-21, gold `rbi:md:2025:0210:v2#p26`

Query: SLR assets shall be maintained by PBs as under: (1) Cash, or;

**Verdict:** correct: same provision, changed, dates match the version history

## 35. temp-c616ef4c33

Direction 13050, paragraph `p12`.

- Old version `rbi:md:2025:0256:v1`, in force 2025-11-28 to 2026-04-01:
  > 12. In case, such contingencies arise as at paragraph 11(2) and (3) above, a bank shall be deemed to be a party to the violation of the provisions of Section 20 of the BR Act, 1949.
- New version `rbi:md:2025:0256:v2`, in force 2026-04-01 to 2026-07-01:
  > 12. *****]

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-c616ef4c33-old` as of 2026-03-31, gold `rbi:md:2025:0256:v1#p12`

Query: In case, such contingencies arise as at paragraph 11(2) and (3) above, a bank shall be deemed to be a party to the violation of the provisions of Section 20

**Verdict:** correct: the provision was deleted in the new version (12. *****]); the old-side query stands, the new side is not asked

## 36. temp-ceae4f9c13

Direction 12959, paragraph `p18`.

- Old version `rbi:md:2025:0345:v4`, in force 2026-06-16 to 2026-07-01:
  > 18. An NBFC shall follow following guidelines for computation of RWAs. (1) Risk-weights, representing degree of credit risk, to be applied to on balance sheet items are as provided in the table below. The value of each on balance sheet asset / item shall be multiplied with applicable risk weights to...
- New version `rbi:md:2025:0345:v5`, in force 2026-07-01 to open:
  > 18. An NBFC shall follow following guidelines for computation of RWAs. (1) Risk-weights, representing degree of credit risk, to be applied to on balance sheet items are as provided in the table below. The value of each on balance sheet asset / item shall be multiplied with applicable risk weights to...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-ceae4f9c13-new` as of 2026-07-01, gold `rbi:md:2025:0345:v5#p18`; `temp-ceae4f9c13-old` as of 2026-06-30, gold `rbi:md:2025:0345:v4#p18`

Query: An NBFC shall follow following guidelines for computation of RWAs.

**Verdict:** correct: same provision, changed, dates match the version history

## 37. temp-d2c6088bb4

Direction 12939, paragraph `p5`.

- Old version `rbi:md:2025:0365:v4`, in force 2026-04-15 to 2026-10-01:
  > 5. The provisions which have been made applicable to HFCs as specified in the following directions, where not contradictory to the contents of these Directions, shall be applicable to HFCs, based on the layer in which the HFC is categorised: (1) Reserve Bank of India (Non-Banking Financial Companies...
- New version `rbi:md:2025:0365:v5`, in force 2026-10-01 to open:
  > 5. The provisions which have been made applicable to HFCs as specified in the following directions, where not contradictory to the contents of these Directions, shall be applicable to HFCs, based on the layer in which the HFC is categorised: (1) Reserve Bank of India (Non-Banking Financial Companies...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-d2c6088bb4-new` as of 2026-10-01, gold `rbi:md:2025:0365:v5#p5`; `temp-d2c6088bb4-old` as of 2026-09-30, gold `rbi:md:2025:0365:v4#p5`

Query: The provisions which have been made applicable to HFCs as specified in the following directions, where not contradictory to the contents of these Directions, shall be applicable to HFCs, based

**Verdict:** correct: same provision, changed, dates match the version history

## 38. temp-d564ac08c3

Direction 13640, paragraph `p4`.

- Old version `rbi:md:2026:0413:v1`, in force 2026-07-31 to 2026-10-01:
  > 4. The Board of the bank / Local Advisory Board (LAB) (in case of a foreign bank) shall approve the following: (1) A well-defined policy for undertaking RBIA. (2) A risk assessment methodology devised by the Internal Audit Department (IAD) of the bank, keeping in view the size and complexity of the ...
- New version `rbi:md:2026:0413:v2`, in force 2026-10-01 to open:
  > 4. The Board of the bank / Local Advisory Board (LAB) (in case of a foreign bank) shall approve the following: (1) A well-defined policy for undertaking RBIA. (2) A risk assessment methodology devised by the Internal Audit Department (IAD) of the bank, keeping in view the size and complexity of the ...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-d564ac08c3-new` as of 2026-10-01, gold `rbi:md:2026:0413:v2#p4`; `temp-d564ac08c3-old` as of 2026-09-30, gold `rbi:md:2026:0413:v1#p4`

Query: The Board of the bank / Local Advisory Board (LAB) (in case of a foreign bank) shall approve the following: (1) A well-defined policy for undertaking RBIA. (2) A risk

**Verdict:** correct: same provision, changed, dates match the version history

## 39. temp-dc7b8b45e3

Direction 13099, paragraph `p27`.

- Old version `rbi:md:2025:0209:v1`, in force 2025-11-28 to 2026-10-01:
  > 27. To improve the manner in which the proceedings of Board Meetings are recorded and followed up, a bank shall provide the following information to its Board: i. a summary of key observations made by the directors which shall be submitted in the next Board meeting; and ii. a detailed recording of t...
- New version `rbi:md:2025:0209:v2`, in force 2026-10-01 to open:
  > 27. [*****]]

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-dc7b8b45e3-old` as of 2026-09-30, gold `rbi:md:2025:0209:v1#p27`

Query: To improve the manner in which the proceedings of Board Meetings are recorded and followed up, a bank shall provide the following information to its Board: i. a summary of

**Verdict:** correct: the provision was deleted in the new version (27. [*****]]); the old-side query stands, the new side is not asked

## 40. temp-df6c33c724

Direction 12992, paragraph `p33`.

- Old version `rbi:md:2025:0312:v1`, in force 2025-11-28 to 2026-07-01:
  > 33. A bank shall follow the guidelines given in Annex I regarding relief measures in areas affected by natural calamities.
- New version `rbi:md:2025:0312:v2`, in force 2026-07-01 to 2026-10-01:
  > 33. [***]

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-df6c33c724-old` as of 2026-06-30, gold `rbi:md:2025:0312:v1#p33`

Query: A bank shall follow the guidelines given in Annex I regarding relief measures in areas affected by natural calamities.

**Verdict:** correct: the provision was deleted in the new version (33. [***]); the old-side query stands, the new side is not asked

## 41. temp-e052f2cfc8

Direction 13123, paragraph `p73`.

- Old version `rbi:md:2025:0186:v1`, in force 2025-11-28 to 2026-10-01:
  > 73.The Reserve Bank has not prescribed any requirement for insurance cover on credit and debit cards. However, in case a card-issuer or a card payment network provides an insurance cover, complimentary or chargeable (with the consent of the cardholder), the card-issuer shall ensure that the relevant...
- New version `rbi:md:2025:0186:v2`, in force 2026-10-01 to open:
  > 73. The Reserve Bank has not prescribed any requirement for insurance cover on credit and debit cards. However, in case a card-issuer or a card payment network provides an insurance cover, complimentary or chargeable (with the consent of the cardholder), the card-issuer shall ensure that the relevan...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-e052f2cfc8-new` as of 2026-10-01, gold `rbi:md:2025:0186:v2#p73`; `temp-e052f2cfc8-old` as of 2026-09-30, gold `rbi:md:2025:0186:v1#p73`

Query: 73.The Reserve Bank has not prescribed any requirement for insurance cover on credit and debit cards.

**Verdict:** correct: same provision, changed, dates match the version history

## 42. temp-e2d6a19250

Direction 12938, paragraph `p159`.

- Old version `rbi:md:2025:0366:v1`, in force 2025-11-28 to 2026-03-10:
  > 159. The extant exposure norms for SPDs are as follows: (1) The exposure shall not exceed 25 per cent of Tier 1 capital as per last audited accounts in case of a single borrower / counterparty and 40 per cent of Tier 1 capital in case of a group borrower / counterparty except for investments in AAA ...
- New version `rbi:md:2025:0366:v2`, in force 2026-03-10 to open:
  > 159. The extant exposure norms for SPDs are as follows: (1) The exposure shall not exceed 25 per cent of Tier 1 capital as per last audited accounts in case of a single borrower / counterparty and 40 per cent of Tier 1 capital in case of a group borrower / counterparty except for investments in AAA ...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-e2d6a19250-new` as of 2026-03-10, gold `rbi:md:2025:0366:v2#p159`; `temp-e2d6a19250-old` as of 2026-03-09, gold `rbi:md:2025:0366:v1#p159`

Query: The extant exposure norms for SPDs are as follows: (1) The exposure shall not exceed 25 per cent of Tier 1 capital as per last audited accounts in case of

**Verdict:** correct: same provision, changed, dates match the version history

## 43. temp-ec26632ccf

Direction 12947, paragraph `p10`.

- Old version `rbi:md:2025:0357:v2`, in force 2026-07-01 to 2026-10-01:
  > 10. In Part A of these Directions, the following definitions shall apply, unless the context otherwise requires: (1) ‘aggregate exposure’ shall include all fund based and non-fund based exposure, including investment exposure; (2) ‘compromise settlement’ shall refer to any negotiated arrangement wit...
- New version `rbi:md:2025:0357:v3`, in force 2026-10-01 to open:
  > 10. In Part A of these Directions, the following definitions shall apply, unless the context otherwise requires: (1) ‘aggregate exposure’ shall include all fund based and non-fund based exposure, including investment exposure; (2) ‘compromise settlement’ shall refer to any negotiated arrangement wit...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-ec26632ccf-new` as of 2026-10-01, gold `rbi:md:2025:0357:v3#p10`; `temp-ec26632ccf-old` as of 2026-09-30, gold `rbi:md:2025:0357:v2#p10`

Query: In Part A of these Directions, the following definitions shall apply, unless the context otherwise requires: (1) ‘aggregate exposure’ shall include all fund based and non-fund based exposure, including investment

**Verdict:** correct: same provision, changed, dates match the version history

## 44. temp-f1f3ba1eda

Direction 13004, paragraph `p11`.

- Old version `rbi:md:2025:0300:v1`, in force 2025-11-28 to 2026-05-15:
  > 11. PNCPS issued by an RCB shall adhere to the following guidelines for to be eligible for inclusion in Tier 1 capital: (1) An RCB is permitted to issue PNCPS at face value to its members or any other person residing within its area of operation, with the prior approval of the Reserve Bank. The RCB ...
- New version `rbi:md:2025:0300:v2`, in force 2026-05-15 to open:
  > 11. PNCPS issued by an RCB shall adhere to the following guidelines for to be eligible for inclusion in Tier 1 capital: (1) An RCB is permitted to issue PNCPS at face value to its members or any other person residing within its area of operation, with the prior approval of the Reserve Bank. The RCB ...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-f1f3ba1eda-new` as of 2026-05-15, gold `rbi:md:2025:0300:v2#p11`; `temp-f1f3ba1eda-old` as of 2026-05-14, gold `rbi:md:2025:0300:v1#p11`

Query: PNCPS issued by an RCB shall adhere to the following guidelines for to be eligible for inclusion in Tier 1 capital: (1) An RCB is permitted to issue PNCPS at

**Verdict:** correct: same provision, changed, dates match the version history

## 45. temp-f421a46b05

Direction 13108, paragraph `p76`.

- Old version `rbi:md:2025:0201:v1`, in force 2025-11-28 to 2026-04-01:
  > 76. The SFB shall put in place a suitable mechanism and structure for the reporting of the customer liability cases to the Board or one of its Committees. The reporting shall, inter alia, include volume / number of cases and the aggregate value involved and distribution across various categories of ...
- New version `rbi:md:2025:0201:v2`, in force 2026-04-01 to 2026-07-01:
  > 76. The SFB shall put in place a suitable mechanism and structure for the reporting of the customer liability cases to the Board or one of its Committees. The reporting shall, inter alia, include volume / number of cases and the aggregate value involved and distribution across various categories of ...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-f421a46b05-new` as of 2026-04-01, gold `rbi:md:2025:0201:v2#p76`; `temp-f421a46b05-old` as of 2026-03-31, gold `rbi:md:2025:0201:v1#p76`

Query: The SFB shall put in place a suitable mechanism and structure for the reporting of the customer liability cases to the Board or one of its Committees.

**Verdict:** wrong: no substantive change; the two versions differ only in typography (a curly for a straight apostrophe), so this is not an amended provision

## 46. temp-f4ba32bbe0

Direction 12990, paragraph `p10`.

- Old version `rbi:md:2025:0314:v1`, in force 2025-11-28 to 2026-04-01:
  > 10. A bank shall, at the minimum, furnish the following information in the ‘Notes to Accounts’. The bank shall note that mere mention of an activity, transaction, or item in the disclosure template does not imply that it is permitted, and the bank shall refer to the extant statutory and regulatory r...
- New version `rbi:md:2025:0314:v2`, in force 2026-04-01 to open:
  > 10. A bank shall, at the minimum, furnish the following information in the ‘Notes to Accounts’. The bank shall note that mere mention of an activity, transaction, or item in the disclosure template does not imply that it is permitted, and the bank shall refer to the extant statutory and regulatory r...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-f4ba32bbe0-new` as of 2026-04-01, gold `rbi:md:2025:0314:v2#p10`; `temp-f4ba32bbe0-old` as of 2026-03-31, gold `rbi:md:2025:0314:v1#p10`

Query: A bank shall, at the minimum, furnish the following information in the ‘Notes to Accounts’. The bank shall note that mere mention of an activity, transaction, or item in the

**Verdict:** correct: same provision, changed, dates match the version history

## 47. temp-f7be70c643

Direction 13096, paragraph `p6`.

- Old version `rbi:md:2025:0212:v1`, in force 2025-11-28 to 2026-10-01:
  > 6. A bank shall pay interest on deposits (other than current account deposits) accepted by them in their domestic account on the terms and conditions specified in these directions: 1. There shall be a comprehensive policy on Interest Rate on Deposits duly approved by the Board of Directors or any Co...
- New version `rbi:md:2025:0212:v2`, in force 2026-10-01 to open:
  > 6. A bank shall pay interest on deposits (other than current account deposits) accepted by them in their domestic account on the terms and conditions specified in these directions: (1) There shall be a comprehensive policy on Interest Rate on Deposits duly approved by the Board of Directors or any C...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-f7be70c643-new` as of 2026-10-01, gold `rbi:md:2025:0212:v2#p6`; `temp-f7be70c643-old` as of 2026-09-30, gold `rbi:md:2025:0212:v1#p6`

Query: A bank shall pay interest on deposits (other than current account deposits) accepted by them in their domestic account on the terms and conditions specified in these directions: 1. There

**Verdict:** correct: same provision, changed, dates match the version history

## 48. temp-f87a604ea5

Direction 13148, paragraph `p12`.

- Old version `rbi:md:2025:0162:v3`, in force 2026-09-22 to 2026-10-01:
  > 12. The Board shall also review the various aspects of non-SLR investments at least on quarterly intervals as specified in paragraph 90(12)(ii).
- New version `rbi:md:2025:0162:v4`, in force 2026-10-01 to open:
  > 12. The Board shall also review the various aspects of non-SLR investments at least on quarterly intervals as specified in paragraph 90(12)(ii). In this respect, the Board shall have the option to delegate its powers to review to its Risk Management Committee.

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-f87a604ea5-new` as of 2026-10-01, gold `rbi:md:2025:0162:v4#p12`; `temp-f87a604ea5-old` as of 2026-09-30, gold `rbi:md:2025:0162:v3#p12`

Query: The Board shall also review the various aspects of non-SLR investments at least on quarterly intervals as specified in paragraph 90(12)(ii).

**Verdict:** correct: same provision, changed, dates match the version history

## 49. temp-f99c26f27d

Direction 13089, paragraph `p172`.

- Old version `rbi:md:2025:0219:v2`, in force 2026-04-01 to 2026-10-01:
  > 172. A PB's officer authorising the locker-hirer to access the locker, after unlocking the first key / password shall not remain present when the locker is opened by the locker-hirer. A PB shall ensure that there is adequate privacy to the locker-hirers in the operations when customers access the lo...
- New version `rbi:md:2025:0219:v3`, in force 2026-10-01 to open:
  > 172. A PB’s officer authorising the locker-hirer to access the locker, after unlocking the first key / password shall not remain present when the locker is opened by the locker-hirer. A PB shall ensure that there is adequate privacy to the locker-hirers in the operations when customers access the lo...

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-f99c26f27d-new` as of 2026-10-01, gold `rbi:md:2025:0219:v3#p172`; `temp-f99c26f27d-old` as of 2026-09-30, gold `rbi:md:2025:0219:v2#p172`

Query: A PB's officer authorising the locker-hirer to access the locker, after unlocking the first key / password shall not remain present when the locker is opened by the locker-hirer.

**Verdict:** wrong: no substantive change; the two versions differ only in typography (a curly for a straight apostrophe), so this is not an amended provision

## 50. temp-fc4e86f37b

Direction 12961, paragraph `p13`.

- Old version `rbi:md:2025:0343:v2`, in force 2025-12-05 to 2026-04-27:
  > 13. The provisions of paragraphs 7 to 12 above shall come into force with effect from January 1, 2026, or from an earlier date as decided by an NBFC in line with its internal policy (referred to as the 'effective date' for the provisions of paragraphs 7 to 12 above). Until such commencement, NBFCs s...
- New version `rbi:md:2025:0343:v3`, in force 2026-04-27 to 2026-07-01:
  > 13. [Deleted].

Claim: these are the same provision in two consecutive versions of the direction, the text changed between them, and each was in force on the dates shown.
Queries built from it: `temp-fc4e86f37b-old` as of 2026-04-26, gold `rbi:md:2025:0343:v2#p13`

Query: The provisions of paragraphs 7 to 12 above shall come into force with effect from January 1, 2026, or from an earlier date as decided by an NBFC in line

**Verdict:** correct: the provision was deleted in the new version (13. [Deleted].); the old-side query stands, the new side is not asked
