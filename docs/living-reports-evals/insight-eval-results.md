# Insight eval: results

Date: 2026-11-15. Method and hypotheses: `insight-eval-preregistration.md` (fixed before the first run). Each arm ran 3 times on each of the three demos, with all rows of the demo data (through December 2025). Recall counts the 14 expressible stories; each question counts once per run.

## Summary

- **The code-only text surfaced more stories than either writer.** A writer often leaves out a claim that states the story, although code listed it. A coverage rule for the two most salient claims closed most of this gap (0.43 for both writers, against 0.48).
- **The larger writer did not surface more.** `gpt-6.1-terra` was at or below `gpt-6-luna`. It also routes the questions, and it asked a clarify question more often.
- **Direct questions find the story; open questions almost never do.** The open questions end in a clarify question, a refusal, or a breakdown that does not hold the story.
- **Three claim fixes raised the code-only recall on direct questions from 0.69 to 0.81.**

## Cited recall (mean of 3 runs; listed recall in brackets)

### Baseline (before the claim fixes)

| Arm | Direct | Topic | Open | All | Cost of 3 runs |
|---|---|---|---|---|---|
| Code only | 0.69 (0.69) | 0.50 (0.50) | 0.14 (0.14) | 0.44 (0.44) | $0.017 |
| `gpt-6-luna` | 0.69 (0.76) | 0.36 (0.50) | 0.12 (0.14) | 0.39 (0.47) | $0.039 |
| `gpt-6.1-terra` | 0.64 (0.79) | 0.48 (0.50) | 0.00 (0.00) | 0.37 (0.43) | $0.324 |

### After the claim fixes

| Arm | Direct | Topic | Open | All | Cost of 3 runs |
|---|---|---|---|---|---|
| Code only | 0.81 (0.81) | 0.50 (0.50) | 0.14 (0.14) | 0.48 (0.48) | $0.017 |
| `gpt-6-luna` | 0.74 (0.81) | 0.36 (0.50) | 0.07 (0.14) | 0.39 (0.48) | $0.037 |
| `gpt-6.1-terra` | 0.69 (0.83) | 0.43 (0.50) | 0.00 (0.00) | 0.37 (0.44) | $0.330 |

The code-only arm costs money only for routing. The standard deviation over runs was 0.04 or less for every "All" value.

### After the coverage rule for two claims

The prompt names the two most salient claims of a section, and code adds a missing one at the start (before: one claim). The code-only arm does not change.

| Arm | Direct | Topic | Open | All | Cost of 3 runs |
|---|---|---|---|---|---|
| `gpt-6-luna` | 0.76 (0.83) | 0.45 (0.50) | 0.07 (0.14) | 0.43 (0.49) | $0.040 |
| `gpt-6.1-terra` | 0.79 (0.86) | 0.50 (0.50) | 0.00 (0.00) | 0.43 (0.45) | $0.319 |

Example (HR story 2, `gpt-6-luna`): "Customer support had the highest absence rate of the 6 departments" was cited in 3 of 9 answers before, and in 6 of 9 after.

### With one follow-up section

The eval adds the section of one follow-up chip to each report, as a click, and scores every section. "First" is the first candidate in vocabulary order (the fixed rule). "Ranked" is the first candidate after the decision model (`d3:4b-q8_0`) orders them by which one most likely explains the section's top claim.

| Arm | Direct | Topic | Open | All | Cost of 3 runs |
|---|---|---|---|---|---|
| Code only, first chip | 0.90 (0.90) | 0.50 (0.50) | 0.14 (0.14) | 0.52 (0.52) | $0.017 |
| Code only, ranked chip | 0.79 (0.86) | 0.50 (0.50) | 0.14 (0.14) | 0.48 (0.50) | $0.017 |
| `gpt-6-luna`, ranked chip | 0.81 (0.88) | 0.45 (0.52) | 0.07 (0.14) | 0.44 (0.52) | $0.061 |

- The ranking by the 4B decision model did not beat the vocabulary order. A follow-up section helped on direct questions only.
- Topic and open questions did not change. Most of their misses are a clarify question or a refusal, so there is no section to follow, or the story needs two steps (a breakdown, then a member of it).
- HR story 1 matched 4 of 9 times with `gpt-6-luna`, but not as the story. The chip "By Tenure band" gave "Attrition rate for Junior, Engineering, Under 6 months rose in every quarter", and a rule accepts a claim on a narrower segment. This is a limit of the rules: a narrower segment can match a story that it does not state.

### Routing of topic and open questions

Two routing rules changed. A movement word ("moving", "changing", "trending") now counts as a time word, so the question gets a series. A plural metric name ("return rates") now names the metric, so "How are return rates moving by category?" gets a report, not a clarify question. Most open questions name no metric, so a clarify question is the intended answer. A new eval option answers it with its first option, as a person who picks the first choice.

| Arm (code only, first chip) | Direct | Topic | Open | All |
|---|---|---|---|---|
| Routing fixes | 0.83 (0.83) | 0.50 (0.52) | 0.14 (0.14) | 0.49 (0.50) |
| Routing fixes, clarify answered | 0.79 (0.79) | 0.50 (0.52) | 0.29 (0.29) | 0.52 (0.53) |

- The routing fixes did not change the topic recall. "How are return rates moving by category?" now gets a report, but its story is in North Apparel, and the question has no region.
- An answered clarify question doubles the open recall (0.14 to 0.29).
- Direct recall moved from 0.90 to 0.83 because two questions that the language model routes got a different route in some runs (run-to-run variation, not the routing rules).

### Gemini Decision 1 as the decision model and the chip ranker

`LR_DECIDE_MODEL=gemini/gemini-decision-1` with `LR_RANK_FOLLOW_UPS=true`, code-only text, the top-ranked chip. Cost of 3 runs: $0.033 (routing calls and decision input).

| Arm | Direct | Topic | Open | All |
|---|---|---|---|---|
| Gemini Decision 1, ranked chip | 0.67 (0.67) | 0.50 (0.67) | 0.14 (0.29) | 0.44 (0.54) |

- This arm listed more stories than any other: listed recall 0.67 on topic questions and 0.29 on open questions (against 0.52 and 0.14). A later run with `d3:4b-q8_0` routing and Gemini ranking (below) shows that the gain came from the routing, not from the ranking: with Gemini as the decision model, the language model routed more questions, and it chose a monthly series for questions such as "How is the fraud rate moving by channel?".
- Direct recall fell to 0.67. Gemini Decision 1 gave lower "answerable" probabilities than `d3:4b-q8_0` (for example 0.36 for "How are return rates moving by category?"), so more questions went to the language model router.

### The local model routes, Gemini Decision 1 ranks the chips

`LR_RANK_MODEL=gemini/gemini-decision-1` ranks the chips while `d3:4b-q8_0` routes. Each answer adds the section of the top-ranked chip. A second rule then changed the routing: a question with a movement word ("changing", "moving") for which the decision model chooses no grain goes to the language model router. Before it, `d3:4b-q8_0` answered "How is revenue changing by channel?" with one number for each channel and no series.

| Arm | Direct | Topic | Open | All | Cost of 3 runs |
|---|---|---|---|---|---|
| Code only | 0.76 (0.76) | 0.50 (0.52) | 0.14 (0.21) | 0.47 (0.50) | $0.022 |
| `gpt-6-luna` | 0.81 (0.81) | 0.48 (0.50) | 0.07 (0.21) | 0.45 (0.51) | $0.076 |
| Code only, movement rule | 0.86 (0.86) | 0.50 (0.60) | 0.14 (0.21) | 0.50 (0.56) | $0.022 |
| `gpt-6-luna`, movement rule | 0.79 (0.81) | 0.55 (0.60) | 0.07 (0.21) | 0.47 (0.54) | $0.078 |

- With the local model routing, the direct recall comes back (0.67 with Gemini routing).
- The Gemini ranking of the chips is close to the vocabulary order (code only: 0.47 against 0.49). The chip ranking stays off by default.
- The movement rule raised the code-only recall to 0.50 and the `gpt-6-luna` recall to 0.47, the best writer result so far.

### Model-proposed claims (part B)

The writer can name up to 3 members of the table (`focus`) and up to 2 comparisons with an earlier section (`cross`) once for each section. Code computes the claims, and the writer drafts again.

| Arm | Direct | Topic | Open | All | Sections with a proposal |
|---|---|---|---|---|---|
| `gpt-6-luna`, first version | 0.76 (0.81) | 0.33 (0.52) | 0.07 (0.14) | 0.39 (0.49) | 10 |
| `gpt-6-luna`, proposed members do not lead | 0.74 (0.76) | 0.50 (0.55) | 0.07 (0.14) | 0.44 (0.48) | 14 |
| `gpt-6.1-terra`, first version | 0.79 (0.86) | 0.57 (0.57) | 0.00 (0.00) | 0.45 (0.48) | 0 |

- In the first version, a proposed member got the lead score of a member that the question names. Its claims took the two covered slots and pushed out the claims that state the story. After the fix (`FOCUS_SCORE`), proposals are neutral for `gpt-6-luna` (0.44, against 0.43 without proposals).
- `gpt-6.1-terra` proposed nothing, so its change against the earlier run (0.43 to 0.45) does not come from proposals. The routing fixes changed in the same round.
- No answer proposed a comparison across sections: each eval question starts a new report. The rules for it have unit tests only.

### Two fixes after the last run (not measured with the eval)

Two misses of the last run have a fix. Neither fix has an eval run yet. A deterministic check on the demo data shows each fix on the question that missed:

- **A change after a date.** For "How did attrition for Junior engineers change after March 2025?", the router query starts on 1 April 2025. Code now removes this start, so the monthly series also covers the months before April 2025. The first two claims are now the Engineering Junior attrition trend and change, and both match story HR 1.
- **A section narrowed to one member.** The chip "Electronics by Brand" gave a section with the claim "Gross margin for Electronics fell sharply", but the code-only text stated the top brand, the margin target, and the total revenue. Such a section now leads with the member's largest change, a change in the wrong direction first. The first claim is now the Electronics gross margin change (story retail 2), and the top brand by revenue is second.

### The fixes measured, and the leading member move (2026-11-16)

Same setup as the run before: the local model routes, Gemini Decision 1 ranks the chips, 3 runs for each arm. This run measures the two fixes above and two more changes:

- **The member that moved most leads.** A question that asks how a value moves by a dimension, and names no member, leads with the largest member change. Example (retail): for "How is revenue changing by channel?", the first claim is now "Revenue for Online rose sharply from $2.4M in 2024 H1 to $4.7M in 2025 H2 (+95.2%)".
- **More checks on the writer's words.** A sentence has at most 25 words and at most 2 claims, and a short list of hard words gets plain words. In this run, the judge also read the title and heading.

Cited recall (listed recall in brackets), mean of 3 runs:

| Arm | Direct | Topic | Open | All | USD (3 runs) |
|---|---|---|---|---|---|
| Code-only text, run before | 0.86 (0.86) | 0.50 (0.60) | 0.14 (0.21) | 0.50 (0.56) | 0.022 |
| Code-only text | 0.86 (0.86) | 0.64 (0.64) | 0.21 (0.21) | 0.57 (0.57) | 0.022 |
| `gpt-6-luna`, run before | 0.79 (0.81) | 0.55 (0.60) | 0.07 (0.21) | 0.47 (0.54) | 0.078 |
| `gpt-6-luna` | 0.81 (0.81) | 0.60 (0.64) | 0.14 (0.21) | 0.52 (0.56) | 0.104 |

- Stories that improved (answers that cite the story, of 9 for each arm and story): HR 1 from 0 to 3 (a change after a date), retail 1 from 3 to 6 (the leading member move), retail 2 from 3 to 6 (a narrowed section), payments 3 from 3 to 6 in the code-only arm (the leading member move).
- Stories that got worse come from the router, not from these changes. Payments 4: the router answered "out of scope" in 2 of 3 runs. Payments 5: in one run the router added a limit of 1 row, and a query with a limit gets no claim for the largest member.
- **The title check cost more than it gave.** In the `gpt-6-luna` arm, code wrote 89 of 204 sections (44%), against 23 of 208 (11%) in the run before. A probe on the 185 accepted sections of the run before: the new sentence rules reject none, but the judge rejects 19 when it also reads the title and heading, against 1 without them. The rejected labels are plain, such as "Trend over time." and "Attrition over time.". After this run, the judge again reads only the paragraphs. The sentence rules stay. The writer numbers above include the title check, and there is no run without it yet.

### Word tokens (after the run above, not measured with the eval)

After this run, the writer writes its own sentences: each value is a `{fact:id}` token, and each finding word is a `{word:id}` token that code writes from a claim ("highest", "below", "rose sharply"). The judge's question R1 now asks whether the writer's own words add a finding. A live check with `gpt-6-luna` on the retail demo (one run for each question):

| Question | Result |
|---|---|
| What are the return rates by region? | passed on the first draft: "North had the highest return rate at 6.30%." … "Overall, the return rate was 5.87%, below the target of 8.00%." |
| How is revenue changing by channel? | passed on the third draft: "For Online, revenue rose sharply from $2.4M to $4.7M, a change of +95.2%." |
| Show gross margin by category. | passed on the first draft: "Electronics gross margin was 25.70%, below the target of 35.00%." |

Before the trend word and the new R1, the second question fell back to code (the writer typed "rose"), and the third failed the judge three times. There is no eval run with word tokens yet.

## The claim fixes

1. **A rate keeps more history.** A rate cannot be summed from its months. Over more than 6 months, the query engine now computes the section's series by quarter, and the claims use the last 6 quarters. Before, a rate kept only its last 6 months. Example (retail story 2, the full data): before, "Gross margin for Electronics was flat (within 2%) from 19.42% in Jul 2025 to 19.21% in Dec 2025", because the fall happened between June and July. After, "Gross margin for Electronics fell sharply from 28.24% in Q1 2025 to 19.15% in Q4 2025 (-32.2%), a change for the worse".
2. **A peak claim.** One month that is at least twice the median of the other months, with each neighbor month at most half of it: "Fraud rate for Digital goods peaked at 5.64% in Aug 2025, 19.7× the median month (0.29%)". Before, the only claims for this series said "fell sharply … a change for the better".
3. **A trend names its flat steps**: "fell in 1 of 5 months and was flat (within 2%) in 4".

A fourth fix corrects the last period of a breakdown by year or by quarter: "Jan–Nov 2025" is now a partial year.

## Hypotheses

| | Result |
|---|---|
| H1: direct > topic > open, in every arm | Supported |
| H2: the writers beat the code-only text on topic and open questions | Not supported: the writers were lower |
| H3: `gpt-6.1-terra` beats `gpt-6-luna` | Not supported |
| H4: listed recall is the same in all arms | Mostly. The differences come from routing: the writer model also routes the questions that the decision model does not route |

## Why stories were missed (after the fixes)

| Cause | Stories | Example |
|---|---|---|
| The writer did not cite a listed claim | HR 2 and 5, payments 2 | HR 2 with `gpt-6-luna`: listed in 9 of 9 answers, cited in 3 |
| The route has no segment that holds the story | most topic and open questions | "How is attrition moving in Engineering?" has no breakdown by job level, so the Junior story cannot appear |
| The route has no "before" period | HR 1 | "How did attrition for Junior engineers change after March 2025?" was routed to April to December 2025 only |
| A clarify question or a refusal | open questions, mostly with `gpt-6.1-terra` | 32 to 36 clarify answers of 135, against 27 for `gpt-6-luna` |
| The rule does not count the peak kind | payments 1 | The peak claim did not exist before the run. It appears when the query breaks the fraud rate down by merchant category and month |
| No claim kind | retail 4 (a seasonal peak) | Marked `expressible: false` before the run |

## What this means for the next steps

- The writer is the weakest step for insight. It drops listed claims that state the story. Model-proposed claims (more model freedom) do not address this. The coverage rule for two claims does, in part.
- Topic and open questions need a route that breaks the metric down by the segment that holds the story. One ranked follow-up did not do this: the 4B ranking was worse than the vocabulary order, and most topic and open misses have no section to follow. The ranking is now off by default (`LR_RANK_FOLLOW_UPS`); the chips keep the vocabulary order.
- These results are on generated data, with single-turn questions.
