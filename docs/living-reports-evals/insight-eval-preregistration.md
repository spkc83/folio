# Insight eval: preregistration

Date: 2026-11-14. This file and the question files are fixed before the first run. A change after the first run is a new, dated file.

## Question

Does a report surface the stories that the demo data plants? And does a larger writer surface more of them than the code-only text?

The routing eval (paper §9.2) measures whether the query is correct. It does not measure whether the report says what matters. This eval measures that.

## Stories and questions

The three demos plant 18 stories (the docstrings of `living_reports/demos/retail.py`, `fraud.py`, and `hr.py`). Three stories start in December 2025 and arrive only with the next data refresh. The eval uses the other 15.

Each story has three questions:

| Level | Meaning | Example (retail story 2) |
|---|---|---|
| direct | names the metric and the segment | "How did the Electronics gross margin change in 2025?" |
| topic | names the metric only | "How is gross margin doing?" |
| open | names neither | "Which product categories need attention?" |

The files are `living_reports/evals/insights_retail.yaml`, `insights_fraud.yaml`, and `insights_hr.yaml`: 15 stories and 45 questions.

## Scoring

Code scores each answer. No model judges it.

- A story is a list of rules. A rule names claim kinds, a metric, a segment, and a direction, a target miss, or the larger member of a comparison. Code computes the direction from the claim's facts, not from its words.
- **Cited:** a claim that the report text cites matches a rule.
- **Listed:** a candidate claim of the report matches a rule, cited or not. Listed and not cited is a choice of the writer. Not listed is a gap in the claims that code makes.
- Retail story 4 (a seasonal peak in November and December) has no claim kind that can state it. It is marked `expressible: false` and is reported apart from the recall.

The scorer is `living_reports/evals/insights.py`. Its tests are in `tests/living_reports/test_insight_eval.py`.

## Arms

| Arm | Writer | Command |
|---|---|---|
| A0 | none: the code-only text (the top claims, one per sentence) | `--arm code` |
| A1 | `gpt-6-luna` | `--arm writer` with `LR_SMALL_MODEL=gpt-6-luna` |
| A2 | `gpt-6.1-terra` | `--arm writer` with `LR_SMALL_MODEL=gpt-6.1-terra` |

All arms:

- The decision model is `d3:4b-q8_0`. It routes first and judges the drafts.
- No frontier rung and no Google key.
- 3 runs for each arm and demo. Each run has a new empty `LR_DATA_DIR`.

## Hypotheses

1. **H1:** cited recall on direct questions is higher than on topic and open questions, in every arm.
2. **H2:** A1 and A2 have a higher cited recall than A0 on topic and open questions. The writer chooses claims; the code-only text takes the top three.
3. **H3:** A2 has a higher cited recall than A1.
4. **H4:** listed recall is the same in all arms. The writer does not change which claims code makes.

## Reported for each arm

- Cited and listed recall, by level and in total.
- The stories that no arm cites, and for each one, whether code listed it.
- Fallback rate, cost, and run time.

## Known before the first run

A check of the rules against the claims of each story's direct query (no question routing, no writer) found 10 of 14 expressible stories listed. Four were not:

- Retail story 1 (Online growth): listed for a query filtered to Online, not for a breakdown by channel.
- Retail story 3 (North Apparel returns in Q4 2025): listed by month, not by quarter, because the partial quarter (Oct–Nov 2025) is not in a quarterly series.
- Payments story 1 (the August 2025 burst) and HR story 1 (Junior Engineering attrition after March 2025): a rate keeps only its last 6 months, and the change claim states the change from June to November. For Digital goods the claim says "fell sharply … a change for the better", because the 5.64% spike sits inside the window.

These are recorded so that the result can separate the claims that code makes from the choices that the writer makes.
