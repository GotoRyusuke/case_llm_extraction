# Manual audit of the shipped run

`output/sample_extraction.csv` (run 1) checked line by line against the 10 source
resumes in `data/sample_resumes.csv`.

**Scope and honesty caveat.** This is a single annotator — the author — reviewing
10 resumes and 43 extracted rows. It is a *qualitative* audit meant to expose
failure modes, not a benchmark. Do not read a precision estimate off it; the
interval around 35/43 is far too wide to be meaningful. The value here is the
catalogue of *kinds* of error, which does generalise.

Counts per case are in `manual_audit.csv`. Totals:

| | count |
|---|---|
| extracted rows | 43 |
| unambiguous correct | 35 |
| clear defects | 5 |
| boundary calls (defensible either way) | 4 |
| observed false negatives | 5 |

## Case-by-case

### P01 — multi-type — clean (14/14)
Every row traces to an explicit clause in the resume. The model correctly split
compounds like "财政局党组书记、局长" into a party row and a government row, and
correctly ignored the two state-owned-enterprise chairmanships and the
non-executive directorship. One judgement call: 市委常委 is stated twice in the
source (once alone, once compounded with 副市长) and the model emitted a single
row. Merging duplicates is what the prompt asks for, so this is defensible, but
it does mean the row cannot be dated to a single spell.

### P02 — full-trajectory — three defects (18/24 unambiguous)
The hardest case: a 24-appointment career spanning township, county, prefecture
and province. Three distinct problems:

1. **Party organ typed as government.** 湖北省委台湾工作办公室 was emitted with
   `inst_type=GOV`. It is a party organ and should be `PARTY`.
2. **Imprecise position.** `中国共产党湖北省委员会` came back with
   `position_std=党委委员`; the source says 省委委员. "党委委员" describes
   membership of *a* party committee generally, not of the provincial committee.
3. **One body, two names.** 湖北省委台湾工作办公室 and 湖北省政府台湾事务办公室 are
   a single institution under two signboards (一个机构两块牌子). The model
   emitted both, double-counting one real post. This is the defect most likely
   to distort downstream counts, and it is invisible to a precision check that
   looks at rows one at a time.

Three further rows are boundary calls rather than errors: 检察院检察长 typed as
GOV (the prosecutorial service is not part of the people's government), and two
行政学院院长 posts (academies are public institutions). The prompt does not settle
these, so the model's choice is not clearly wrong — but the *result* is that the
same underlying kind of post is typed inconsistently across the sample.

### P03, P04 — clean (1/1 each)
Textbook cases. P04 is the better illustration of precision: the resume is dense
with professional-body offices, a visiting professorship, a justiceship of the
peace and a raft of directorships, and exactly one row came back — the CPPCC
national committee membership.

### P05 — ambiguous body (1 row, defensible)
The single extraction is a discipline-inspection committee post in a division of
the Xinjiang Production and Construction Corps. The Corps is a combined
party-government-military-enterprise entity, so "is this a local party organ or
an internal enterprise committee?" has no clean answer under the current spec.
Runs 2 and 3 returned zero rows here; see the stability report.

### P06 — negative control — correct (0 rows)
A resume with arbitration panels, a law firm partnership and a notaries'
association vice-chairmanship produced an empty list, as it should.

### P07 — boundary: judiciary (0 rows)
The only candidate was a temporary posting (挂职) to a court. The prompt defines
GOV as people's governments and their departments, so excluding it is
spec-correct — but courts and procuratorates are a genuine blind spot: the spec
never says whether the judiciary counts, and P02 shows the model including a
procuratorate under the same spec. **This is a spec gap, not a model failure.**

### P08 — generic descriptor as a post (0/2 correct)
Both rows carry `position_std=干部` ("cadre"), which is not a position. The source
says "任北京市委商贸工委干部" — the person held *a post* there without the source
naming it. The prompt already covers this: it instructs the model to emit
「任职（职务不详）」 when an institution is named but no title is given. Runs 2 and 3
did exactly that. So this is an unstable compliance failure, not a spec gap.

### P09 — boundary: mass organisation — correct (0 rows)
The resume's only political-adjacent item is a delegate slot at the national
congress of the science and technology association. The spec excludes mass
organisations (群团组织), listing 科协 by name. Returning zero is right. The
historical full-corpus run *did* extract this as a PARTY appointment, which is a
false positive — a useful reminder that a prompt rule is a tendency, not a
guarantee.

### P10 — boundary: SOE party committees — recall failure (0 rows)
The worst case. It contains legitimate government posts — three spells as a
division chief or deputy chief at the People's Bank of China, which is a
component ministry of the State Council, and 处长/副处长 are explicitly
leadership posts under the prompt. The model returned nothing, apparently
collapsing the whole resume into "state-owned enterprise, exclude". Runs 2 and 3
went the other way and extracted five internal party-committee posts at a
central SOE, which the spec excludes. So this case is wrong in *both* directions
depending on the draw, and it never gets the central-bank posts right.

## What the audit suggests

- **The spec, not the model, is the binding constraint.** Every unstable case
  (P05, P08, P10) is one where the prompt's inclusion rule is ambiguous for the
  entity in question — the judiciary, corporations-as-party-organs, academies-as-
  government, SOE party committees. Where the spec is crisp (P03, P04, P06) the
  output is stable and correct.
- **Row-level precision is not enough.** The 一个机构两块牌子 double count in P02 is
  invisible to per-row checks and would inflate any count of a person's
  appointments. Validate at the entity level, not the row level.
- **Naming normalisation needs its own pass.** The model renames the same
  organisation differently across runs: 中共乐山市委 / 中共乐山市委员会, and
  湖北省人民政府国有资产监督管理委员会党委 /
  中国共产党湖北省人民政府国有资产监督管理委员会委员会. Treat the model's `org_std` as a
  draft, then canonicalise against your own gazetteer. Beware doubled markers
  (`…委员会委员会` and `…委员会党委` name one body): collapsing them one at a time
  leaves `…委委` against `…委`, which never compare equal.
- **Do not treat temperature 0 as reproducible.** See
  `output/stability/stability_report.md`.
