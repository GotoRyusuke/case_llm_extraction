# Case study: LLM-based extraction of political appointments from executive biographies

A small, self-contained example of using an LLM to turn unstructured Chinese
executive biographies into a structured event table.

The task: read a Chinese listed-company executive's CV and extract every
**political appointment held outside the firm** — government, Communist Party,
people's congress, and consultative conference posts — as one row per
appointment, with institution type, administrative level, province/city/county,
dates, and tenure status.

This folder is a reduced copy of a production pipeline that was run over
285,511 biographies. Everything here runs on 10 sample resumes in about half a
minute, for under one US cent.

> **Two notes on the cost figures before you read them as a budget.**
>
> 1. **The production run cost far less than the prices quoted here.** The full
>    285,511-record run was executed on `deepseek-v4-flash` at prices that no
>    longer apply — DeepSeek has adjusted its schedule several times since. The
>    per-token prices in §7 are the *current* ones, applied to a sample that was
>    deliberately chosen to be appointment-rich, so the extrapolated total is a
>    present-day upper bound, not a reconstruction of what was actually spent.
> 2. **These figures are a proof of concept, not a production budget.** Ideally, after
>    the **Business School AI Hub** deploys some local models, we can implement this task
>    locally, where the marginal inference cost is negligible (electricity
>    bills will be covered by the university, hopefully.) Note, though, that the extraction
>    quality and the run-to-run variability reported in §5–§6
>    are properties of the DeepSeek model tested here; a different model (e.g.
>     from Z.ai or Qwen) needs
>    its own validation before those numbers can be assumed to carry over.

---

## 1. Why this is not a regex problem

The information is genuinely unstructured, and the same real-world fact surfaces
in many surface forms:

```
自2004年3月至2008年9月担任四川省乐山市财政局党组书记、局长
自2015年6月至2017年1月担任雅安市委常委、副市长
曾任松滋县老城公社党委副书记、书记
```

One clause can contain two appointments of *different* institution types
(党组书记 is party, 局长 is government). One appointment is spread across a
parenthetical aside. Some appointments are stated with no title at all
("先后在省发改委、省政府办公厅工作"), and some must be *excluded* even though they
look political (民主党派, 群团组织, 企业内部党委, 荣誉头衔).

A rule-based extractor needs a gazetteer of every government body, every party
organ and every title suffix, plus a disambiguation layer for the exclusion
rules. An LLM does this zero-shot from a written specification, which is what
makes the specification — the prompt — the thing worth getting right.

---

## 2. Task specification

Four target categories, deliberately narrow:

| Code | Category | Examples |
|---|---|---|
| `GOV` | Government | 省长, 市长, 县长, 局长, 厅长, 处长, 科长, 镇长, 主任 (government line) |
| `PARTY` | Communist Party organs | 省委/市委/县委书记、常委、委员, 纪委书记, 组织/宣传/统战部长, 党组书记, 党代表 |
| `NPC` | People's congresses | 人大代表, 人大常委会委员, 人大主任/副主任 |
| `CPPCC` | Consultative conferences | 政协委员/常委, 政协主席/副主席 |

Explicitly **excluded**, because they are not state political office and would
otherwise contaminate a political-connection measure: other parties
(民主党派: 农工党, 民建, 民盟, 民革, 致公党, 九三学社, 台盟, 民进); mass organisations
(群团组织: 妇联, 工商联, 共青团, 工会, 科协, 文联, …); rank-only civil-service
grades (科员, 主任科员, 调研员, 巡视员) as distinct from leadership posts
(科长, 处长, 局长); internal party committees of firms and SOEs unless they are
local party organs; universities, hospitals, academies and industry
associations; honours and advisory titles; and the firm's own directorships.

The full text lives in `prompts/system_prompt.txt` — read that file before the
code. The extraction schema is documented at the bottom of the prompt, and the
controlled vocabularies are mirrored in `code/extract_demo.py`.

---

## 3. Architecture: an LLM layer and a deterministic layer

The design principle is to let the model do only the part that needs language
understanding, and to make everything checkable deterministic.

```
resume text
    │
    ├─ LLM layer (prompt → JSON schema)
    │    semantic decisions: which spans are appointments, which
    │    category, what level, what dates
    │
    ├─ deterministic post-filter
    │    • drop inst_type == OTHER
    │    • drop rank-only grades (科员|调研员|巡视员|办事员 …)
    │      so a model slip cannot leak noise into the table
    │
    ├─ deterministic normalisation
    │    • province forced to carry its suffix (34-entry map)
    │    • doubled suffixes collapsed (山东省省 → 山东省)
    │    • direct-administered municipalities: 区/县 pushed to county level
    │
    └─ event table (one row per appointment)
```

Three prompt decisions matter more than the rest, and all three were learned the
hard way:

1. **Turn implicit reasoning off.** The request body sends
   `thinking: {"type": "disabled"}`. With reasoning left on, the model spends its
   token budget on `reasoning_content`, the JSON comes back truncated
   (`finish_reason: length`), and — measured — output tokens rise about sixfold.
2. **Force JSON mode** (`response_format={"type":"json_object"}`), with a
   graceful fallback that retries without it if the model rejects the parameter.
3. **Write the exclusion list explicitly, by name.** Telling the model what
   *not* to extract, with concrete examples (农工党, 妇联, 名誉会长), does more
   for precision than any amount of schema description.

The prompt also instructs the model to emit 「任职（职务不详）」 for institutions named
without a title, and to merge multi-session appointments
(第十一、十二、十三届全国人大代表) into one row.

### API details, checked against the current docs

The request body is deliberately small. Every field below was verified against
`https://api-docs.deepseek.com` rather than assumed:

| Field | Value | Why |
|---|---|---|
| `model` | `deepseek-flash` | Current name. The legacy `deepseek-v4-flash` is still accepted, but that model is *retired* and such requests are served by DeepSeek-V4.1-Flash, so the old name buys nothing. `deepseek-v4-pro` is the larger alternative. |
| `thinking` | `{"type": "disabled"}` | Thinking mode is **on by default**. Must be turned off explicitly. Equivalent alternative: `"reasoning_effort": "none"`. |
| `response_format` | `{"type": "json_object"}` | JSON mode. The docs warn you must *also* instruct the model to emit JSON in a system message, or it can stream whitespace until it hits the token cap. |
| `temperature` | `0.0` | Only takes effect in non-thinking mode; thinking mode silently ignores it. |
| `max_tokens` | `10000` | Still the correct name (not `max_completion_tokens`). A ceiling, not a target — the API maximum is 384K. |

Two fields were dropped as noise: `stream: false` is already the default, and
the request needs no `tools`, `stop` or penalty parameters. There is no
`reasoning_content` to strip because thinking is off.

---

## 4. The sample

Ten biographies, chosen to span the decision space rather than to flatter the
method. Six are intended to be clean; four are boundary cases where the
specification is ambiguous. See `data/sample_index.csv`.

| Case | Resume | Demonstrates |
|---|---|---|
| P01 | 588 ch | Four institution types in one CV; compound clauses split correctly |
| P02 | 474 ch | Full career, township → province; 24 appointments; level inference |
| P03 | 891 ch | One clean NPC membership among many association roles |
| P04 | 806 ch | One CPPCC membership buried in a dense finance career |
| P05 | 571 ch | Exclusions: union, women's federation, youth league all present |
| P06 | 320 ch | **Negative control** — correct output is an empty list |
| P07 | 880 ch | Boundary: is a temporary court posting "government"? |
| P08 | 1037 ch | Boundary: "干部" appears where a job title should be |
| P09 | 1068 ch | Boundary: science-association delegate — a mass organisation |
| P10 | 349 ch | Boundary: SOE internal party committees |

---

## 5. Results

`output/sample_extraction.csv` is the shipped run. Every extracted row carries
`source_snippet` holding the full resume text it came from, so any row can be
traced back to its evidence.

**43 appointments from 10 resumes.**

| | |
|---|---|
| wall clock | 32.6 s (3.3 s per resume) |
| prompt tokens | 19,963 — of which **91.7% were cache hits** |
| completion tokens | 6,577 |
| estimated cost | **US$0.0085** at peak rates, US$0.0043 off-peak |
| system prompt | 2,977 characters, ~1,950 tokens per call |

The system prompt is re-sent on every call and is roughly the size of the
resumes themselves, so **92% of the input is a cache hit on the constant
prefix** and the input side of the bill is almost nothing. What actually costs
money is the output: 6,577 completion tokens at US$1.20/M is 93% of this run's
cost. See §7.

### Manual audit

Every row of `output/sample_extraction.csv` was checked against its source
résumé. `review/audit.md` gives the case-by-case reasoning; the counts are in
`review/manual_audit.csv`. **14 issues across 43 extracted rows:**

| | count |
|---|---|
| extracted rows | 43 |
| unambiguous correct | 35 |
| clear defects | 5 |
| boundary calls (defensible either way) | 4 |
| observed false negatives | 5 |

**Clear defects — 5.** Each of these would corrupt a downstream count:

| Case | Problem |
|---|---|
| P02 | A party organ, `省委台湾工作办公室`, typed `GOV`. It belongs to `PARTY`. |
| P02 | `position_std=党委委员` where the source says 省委委员 — "党委委员" is membership of *a* committee, not of the provincial one. |
| P02 | **One body, two signboards.** `省委台湾工作办公室` and `省政府台湾事务办公室` are a single institution (一个机构两块牌子); the model emitted both, counting one post twice. |
| P08 | `position_std=干部` — "cadre" is not a post title. The source names an institution without naming a post, which the prompt says to record as 「任职（职务不详）」. |
| P08 | As above, on the second row of the same résumé. |

The P02 double count is the most dangerous of the five. Both rows look
individually plausible, so **no row-by-row check catches it**, yet it inflates
any count of how many appointments a person holds.

**Boundary calls — 4.** The specification is silent, so the model's choice is
not clearly wrong; the cost is that the same *kind* of post gets typed
inconsistently across the sample:

| Case | Problem |
|---|---|
| P02 | A procuratorate head typed `GOV`, though a procuratorate is not part of the people's government. |
| P02 ×2 | Two 行政学院院长 posts typed `GOV`, though academies are public institutions. |
| P05 | A discipline-inspection post in a Xinjiang Production and Construction Corps division. The Corps is simultaneously party, government, military and enterprise, so "local party organ, or internal enterprise committee?" has no clean answer. |

**False negatives — 5:**

| Case | Missed |
|---|---|
| **P10** | **Three central-bank spells** (中国人民银行 处长/副处长). The People's Bank is a State Council ministry and 处长/副处长 are named in the prompt as leadership posts, but the model returned nothing — apparently collapsing the whole résumé into "SOE, exclude". |
| P01 | One 市委常委, stated twice in the source and emitted as a single row. Merging is what the prompt asks for, but the row can no longer be dated to one spell. |
| P07 | A temporary court posting (挂职). Excluding it is spec-correct — see the boundary note above. |

P10 is the worst case, and it fails in *both* directions depending on the draw:
this run missed the central-bank posts, while runs 2 and 3 instead extracted
five internal party-committee posts at a central SOE, which the spec excludes. It
never gets the case right.

**The binding constraint is the specification, not the model.** Where the
prompt's inclusion rule is crisp (P03, P04, P06), output is stable and correct.
Where it is silent — the judiciary, SOE party committees, academies — the model
oscillates. The productive response is to tighten the spec, not to shop for a
better model.

These same cases are also the ones that move between draws. P05, P08 and P10 are
the worst: they have **no appointment at all in common** across the four runs in
§6. P01 and P02 vary too, though far more mildly.

---

## 6. Reproducibility: temperature 0 is not deterministic

This is the finding most worth carrying to other projects.

The script runs at `temperature: 0.0`. Four independent runs of the same ten
resumes, same prompt, same model, produced **43, 45, 47 and 48 appointments**.
Agreement:

| comparison level | sample Jaccard | identical cases |
|---|---|---|
| exact strings | **0.49** | 5/10 |
| organisation names normalised | **0.60** | 5/10 |
| entity level (type, post, place, level) | 0.59 | 5/10 |

Pairwise at the exact level: run1–run2 = 0.54, run1–run3 = 0.70, run1–run4 =
0.65, run2–run3 = 0.80, run2–run4 = 0.69, run3–run4 = 0.86.

Two conclusions, and the second is the one people miss:

1. **A single pass is a single draw.** Any published count from one pass carries
   sampling variance that a temperature setting does not remove. Budget for
   repeated runs, and vote on the entity rather than on the string.
2. **Raw string agreement understates reliability.** Most of the string-level
   disagreement is orthographic. The model wrote the same provincial
   state-asset commission two ways across runs —
   `湖北省人民政府国有资产监督管理委员会党委` and
   `中国共产党湖北省人民政府国有资产监督管理委员会委员会` — and the same city committee as
   `中共乐山市委` and `中共乐山市委员会`. That is a *normalisation* problem, not a
   *comprehension* one, and it is worth about a fifth of the apparent
   instability: exact-string agreement is 0.49 but rises to 0.60 once names are
   canonicalised. Reporting only the string-level figure would have overstated
   the instability.

   The trap worth knowing about is the **doubled marker**: `…委员会委员会` and
   `…委员会党委` name one body, but collapsing each marker separately leaves `…委委`
   against `…委`, which never compare equal. Runs of markers have to collapse
   together in a single pass. Our first implementation did this one marker at a
   time and silently understated agreement — the kind of bug that is invisible in
   the output and only surfaces if you check the normaliser against real strings
   from your own data.

Five cases differ at every comparison level, but they are not equally unstable.
Three — **P05, P08 and P10** — score Jaccard 0.00: *not one appointment survives
across all four runs*. These are precisely the spec-ambiguous cases from §5. The
other two, P01 and P02, differ far more mildly (0.87 and 0.74 at entity level),
and their disagreement is largely orthographic.

The four raw draws and their usage records are kept in `output/stability/` so
the comparison can be re-derived.

**Caveat on comparing to the production run.** The full-corpus run over 285,511
resumes was executed a month earlier with the same prompt and model name, with several rounds of post hoc refinement.
Do run a couple of pilot tests before you exhaust your budget!

---

## 7. Cost and throughput at scale

Measured on this sample, per resume: ~2,000 prompt tokens (~92% cache hits),
~660–730 completion tokens, 3.3 s wall clock sequentially.

Two facts dominate the bill, and neither is intuitive:

- **Cached input is ~50× cheaper than uncached input.** Because the system
  prompt is constant, ~92% of the input is a cache hit, and the input side of
  the bill becomes almost nothing.
- **Output costs ~4× an uncached input token.** So the biggest single saving is
  turning thinking mode off — measured at 355 reasoning tokens against 71 answer
  tokens on identical input, a **5.9× reduction in output**.

Extrapolating to the full corpus of 285,511 biographies:

| | |
|---|---|
| prompt tokens | ~5.7 × 10⁸, 92% cached |
| completion tokens | ~2.1 × 10⁸ |
| cost at peak rates | **~US$265** |
| cost at off-peak rates | **~US$133** |
| sequential wall clock | ~11 days |

> **Caveat on the extrapolation.** The ten sample resumes were deliberately
> chosen to be appointment-rich — two of them yield 14 and 24 appointments — so
> output-per-resume here sits well above a random draw from the corpus. Read the
> dollar figures as an upper bound. The *direction* of the finding, that output
> dominates cost, is the reliable part.

**Cost levers, largest first:**

1. **Turn thinking mode off** (§3). 5.9× fewer output tokens for no loss on a
   pure extraction task. This one is not close.
2. **Keep the system prompt byte-identical** so the prefix cache hits. Its
   determinism matters far more than its length.
3. **Run off-peak**, which is half price: everything outside 01:00–04:00 and
   06:00–10:00 UTC on weekdays, plus all weekend and Chinese public holidays.
   In Beijing time those peak windows are 09:00–12:00 and 14:00–18:00, so **the
   Chinese working day is peak** — a job that runs during office hours pays
   double. Of the four runs in this folder, the three that fell in office hours
   cost 0.85–0.91 cents each, and the one that fell off-peak cost 0.47.
4. **Raise concurrency.** The sequential figure above is ~11 days, but the flash
   model's concurrency limit is 2,500, so wall clock is not a real constraint.

---

## 8. Running it

Requires Python 3 with `requests`, and your own DeepSeek API key. No key is
stored anywhere in this folder.

The script resolves its paths from its own location, so **it runs from any
working directory** — you do not have to `cd` first:

```bash
python case_llm_extraction/code/extract_demo.py        # fine
cd /tmp && python /path/to/code/extract_demo.py        # also fine
```

Below, `python` means whatever your interpreter is called. If `python` is not on
your `PATH` (common on Windows), substitute the full path to your own
interpreter, e.g. `"C:/Python312/python.exe"`.

### Quick start

```bash
# 1. provide a key -- never commit this file
cp .env.example .env        # then edit .env and paste your key
#    alternative:  export DEEPSEEK_API_KEY="sk-..."

# 2. smoke test on 3 resumes (a fraction of a cent)
#    writes output/*.limit3.* -- it cannot touch the shipped reference
python code/extract_demo.py --limit 3

# 3. full run over all 10 resumes (~US$0.0085, see §5)
python code/extract_demo.py
```

### Command reference

`code/extract_demo.py` writes `output/sample_extraction.csv`, `.json`,
`usage.json` and `run.log`.

| Command | What it does |
|---|---|
| `python code/extract_demo.py` | Extract all 10 sample resumes |
| `python code/extract_demo.py --limit 3` | Only the first 3 — smoke test, writes `output/*.limit3.*` |
| `python code/extract_demo.py --api-key sk-...` | Pass the key inline instead of via env/.env |
| `python code/extract_demo.py --help` | Show usage |

Key resolution order is `--api-key`, then `.env`, then the
`DEEPSEEK_API_KEY` environment variable. Two environment variables override the
defaults, which is handy for pointing at a different model without editing code:

```bash
DEEPSEEK_MODEL=deepseek-v4-pro python code/extract_demo.py --limit 3
DEEPSEEK_BASE_URL=https://api.deepseek.com python code/extract_demo.py
```

> **One footgun.** A full run (no `--limit`) overwrites
> `output/sample_extraction.*`, which is the shipped reference output. Copy it
> aside first if you want to keep it. A `--limit` run is safe — it writes
> `output/*.limit<N>.*` instead.

---

## 9. Provenance and licence

- **Source.** The resumes are public disclosure text from CSMAR's director and
  officer database (董监高个人特征) — the biography field as filed in
  Chinese listed companies' annual reports.
- **Sample construction.** Person identifiers and stock codes are replaced with
  placeholders (`P01`…`P10`, `A01`…`A10`) and personal names are replaced with
  synthetic ones applied consistently across each biography, so that no
  fabricated name is attached to a real person's career. Organisation names
  inside the résumé text are kept as written, since the extractor needs them for
  context.
- **Licence.** CSMAR data is redistributed under its own terms. You may get access to it via CU Lib.

---

## 10. Contents

```
case_llm_extraction/
├── README.md                        this document
├── .env.example                     key template -- copy to .env, never commit
├── prompts/
│   └── system_prompt.txt            the full extraction specification
├── data/
│   ├── sample_resumes.csv           10 biographies (input)
│   └── sample_index.csv             which case demonstrates what
├── code/
│   └── extract_demo.py              extraction pipeline (env-var key only)
├── output/
│   ├── sample_extraction.csv        one row per appointment (shipped run)
│   ├── sample_extraction.json       same, nested per person, with counts
│   ├── usage.json                   tokens, cache split, latency, cost estimate
│   └── stability/
│       ├── run1.json … run4.json    four independent draws
│       ├── run1_usage.json …        their usage records
│       │                            (run4 is the only one with true
│       │                             cache hit/miss accounting)
│       ├── run3.log, run4.log       per-resume traces
│       └── stability_report.md      agreement tables for §6
└── review/
    ├── audit.md                     case-by-case manual review
    └── manual_audit.csv             counts per case
```

## 11. Reusing this

The pattern generalises to any extraction task over semi-structured Chinese
text where a written specification can stand in for an annotation guide:

1. Write the inclusion **and exclusion** rules as prose, with named examples.
2. Define a strict JSON schema, and say "null when unsure" explicitly.
3. Add a deterministic post-filter for the noise classes you can enumerate.
4. Normalise entity names in code, not in the prompt.
5. **Run it several times before trusting a number, and compare at the entity
   level, not the string level.**

Steps 4 and 5 are the two that a first implementation usually skips.

> **A final note: What has already been built.**
>
> This folder is only the extraction *method*, demonstrated on 10 CVs. The
> full dataset it belongs to is already complete, in two layers:
>
> - **Appointment-level events** — 285,511 CVs extracted into 71,943
>   political appointments, deduplicated and refined across CV versions down to
>   **30,678** events, covering **20,381 executives at 4,249 listed firms**.
> - **Political-connection panels** at three grains: firm-year-person
>   (**1,363,767** rows), firm-year (**74,458**), and a firm-year panel anchored
>   to each firm's registered address (**58,263**). The anchored panel classifies
>   every appointment as local / same-province-other-city / cross-province /
>   central, and separates directors from executives. 12.5% of person-years
>   carry a political appointment and 78.2% of firm-years have at least one
>   politically connected director; under the firm-anchored definition, **42.3%
>   of firm-years have an out-of-province connection**.
>
>   (available on request).
