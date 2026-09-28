# Run-to-run stability

Same 10 resumes, same model, same prompt, temperature 0, 4 independent runs.

## Comparison level: `exact`

| case | n (run1) | n (run2) | n (run3) | n (run4) | Jaccard | identical |
|---|---|---|---|---|---|---|
| P01 | 14 | 14 | 14 | 14 | 0.56 | **no** |
| P02 | 24 | 22 | 24 | 24 | 0.64 | **no** |
| P03 | 1 | 1 | 1 | 1 | 1.00 | yes |
| P04 | 1 | 1 | 1 | 1 | 1.00 | yes |
| P05 | 1 | 0 | 0 | 0 | 0.00 | **no** |
| P06 | 0 | 0 | 0 | 0 | 1.00 | yes |
| P07 | 0 | 0 | 0 | 0 | 1.00 | yes |
| P08 | 2 | 2 | 2 | 2 | 0.00 | **no** |
| P09 | 0 | 0 | 0 | 0 | 1.00 | yes |
| P10 | 0 | 5 | 5 | 6 | 0.00 | **no** |

**Identical across all runs: 5/10 resumes.** Sample-level Jaccard = 30/61 = **0.49**.

## Comparison level: `orgnorm`

| case | n (run1) | n (run2) | n (run3) | n (run4) | Jaccard | identical |
|---|---|---|---|---|---|---|
| P01 | 14 | 14 | 14 | 14 | 0.75 | **no** |
| P02 | 24 | 22 | 24 | 24 | 0.77 | **no** |
| P03 | 1 | 1 | 1 | 1 | 1.00 | yes |
| P04 | 1 | 1 | 1 | 1 | 1.00 | yes |
| P05 | 1 | 0 | 0 | 0 | 0.00 | **no** |
| P06 | 0 | 0 | 0 | 0 | 1.00 | yes |
| P07 | 0 | 0 | 0 | 0 | 1.00 | yes |
| P08 | 2 | 2 | 2 | 2 | 0.00 | **no** |
| P09 | 0 | 0 | 0 | 0 | 1.00 | yes |
| P10 | 0 | 5 | 5 | 6 | 0.00 | **no** |

**Identical across all runs: 5/10 resumes.** Sample-level Jaccard = 34/57 = **0.60**.

## Comparison level: `entity`

| case | n (run1) | n (run2) | n (run3) | n (run4) | Jaccard | identical |
|---|---|---|---|---|---|---|
| P01 | 14 | 14 | 14 | 14 | 0.87 | **no** |
| P02 | 21 | 19 | 21 | 21 | 0.74 | **no** |
| P03 | 1 | 1 | 1 | 1 | 1.00 | yes |
| P04 | 1 | 1 | 1 | 1 | 1.00 | yes |
| P05 | 1 | 0 | 0 | 0 | 0.00 | **no** |
| P06 | 0 | 0 | 0 | 0 | 1.00 | yes |
| P07 | 0 | 0 | 0 | 0 | 1.00 | yes |
| P08 | 2 | 2 | 2 | 2 | 0.00 | **no** |
| P09 | 0 | 0 | 0 | 0 | 1.00 | yes |
| P10 | 0 | 5 | 5 | 6 | 0.00 | **no** |

**Identical across all runs: 5/10 resumes.** Sample-level Jaccard = 32/54 = **0.59**.

## Pairwise agreement (exact level)

- run1 vs run2: Jaccard **0.54** (43 vs 45 appointments)
- run1 vs run3: Jaccard **0.70** (43 vs 47 appointments)
- run1 vs run4: Jaccard **0.65** (43 vs 48 appointments)
- run2 vs run3: Jaccard **0.80** (45 vs 47 appointments)
- run2 vs run4: Jaccard **0.69** (45 vs 48 appointments)
- run3 vs run4: Jaccard **0.86** (47 vs 48 appointments)
