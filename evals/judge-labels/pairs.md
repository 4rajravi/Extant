# Judge hand labels

Each pair is exactly what the groundedness judge was shown. Complete the **Human verdict:** line with `supported` or `unsupported`, judging only whether the paragraphs support the claim. Phase 6 computes the judge's agreement rate from this file and reports it beside every metric that uses the judge.

## 1. full_answer_not_legal_advice

```
Claim: If a bank does not meet the paragraph 7 eligibility criteria, no special dispensation will be given for declaring a dividend for that period.

Paragraphs:
14. If a bank does not meet the eligibility criteria as per paragraph 7 above, no special dispensation will be given for declaration of dividend or for remittance of profits for that period.
```

- Judge verdict: supported
**Human verdict:** 

## 2. full_answer_third_person

```
Claim: The amount of dividend an eligible Indian-incorporated bank may pay depends on its CET 1 ratio. In aggregate, it cannot exceed 75% of PAT.

Paragraphs:
8. A bank incorporated in India which satisfies the eligibility criteria laid down in paragraph 7 above, may declare and pay dividend up to the limits prescribed under Table 1 below, but in aggregate not exceeding 75% of the PAT for the period for which the dividend is being proposed.
Table 1
Bucket | CET 1 ratio as at the end of previous FY | Dividend allowed as a % of adjusted PAT for the period
B1 | Up to (8 + z)% | 0
B2 | Above (8 + z)% and up to (10 + z)% | 20
B3 | Above (10 + z)% and up to (12 + z)% | 30
B4 | Above (12 + z)% and up to (14 + z)% | 40
B5 | Above (14 + z)% and up to (16 + z)% | 50
B6 | Above (16 + z)% and up to (17 + z)% | 60
B7 | Above (17 + z)% and up to (18 + z)% | 70
B8 | Above (18 + z)% and up to (19 + z)% | 80
B9 | Above (19 + z)% and up to (20 + z)% | 90
B10 | Above (20 + z)% | 100
Note: ‘z’ in Table 1 refers to the respective applicable D-SIB buffer. ‘z’ shall be zero for a bank not classified as D-SIB.
```

- Judge verdict: supported
**Human verdict:** 

## 3. full_answer_third_person

```
Claim: The Reserve Bank may restrict dividend distribution where a bank does not comply with applicable laws, regulations or guidelines.

Paragraphs:
13. The Reserve Bank reserves the right to place restrictions on distribution of dividend or remittance of profit where a bank is found to be non-compliant with the applicable laws, regulations / guidelines issued by the Reserve Bank.
```

- Judge verdict: supported
**Human verdict:** 

