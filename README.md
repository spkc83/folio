# Folio

Folio is an educational demo app for conversational analytics. You ask a business question in plain words. Folio answers with an interactive HTML report: numbers, charts, and a few sentences that say what the numbers mean. A small language model writes the sentences, and code checks every statement about the data before it shows.

Folio runs on generated demo data. It is a project to learn from and to experiment with. It is not for production use.

## What you can do

- **Ask a question.** Type "Which channel drives the most revenue?". Folio picks the metric and the breakdown from a semantic model, runs the query, and writes a report.
- **Read a checked report.** Each report has four views: Narrative, Dashboard, Decision brief, and Evidence. Each number points to the query that made it. Each statement about the numbers is a claim that code computes and checks. Example: "Store had the highest revenue of the 3 channels ($24.7M, 58.33% of $42.4M)".
- **See what is proven and what is not.** Select a citation number to see the evidence for a claim. For each section, the Evidence view shows three lines: the numbers (checked by code), the words between them, and what the text leaves out. When only a model checked the words, the line says "Probable, not proven". When nothing checked them, it says "Not reviewed".
- **Explore with no model call.** Filter the report, select a bar, or drag across a line. Example: select Online, and the gross margin tile changes from 33.46% to 33.38%.
- **Ask a follow-up.** Chips under each section suggest the next view, such as "By Region" or "Electronics by Brand".
- **Edit and publish.** Change a chip, shorten a section, or edit the words. An owner publishes a checked edition, and its questions become certified questions on the home page.
- **Follow the data.** Load the next data snapshot. Folio checks each claim again and marks the claims that are no longer true.
- **Build a semantic model.** On the model page, connect a source, run discovery, and confirm each proposed metric and dimension.

## Quick start

1. Install the Python packages: `uv venv --python 3.12`, then `uv pip install --python .venv/bin/python -e ".[dev]" -c constraints.txt`.
2. Install Deno. The sandbox looks for `deno` on the `PATH` and then in `~/.deno/bin`.
3. Copy `.env.example` to `.env`.
4. In `.env`, set `LR_SMALL_MODEL`, for example `ollama/qwen2.5-coder:1.5b`. To use a cloud model, set the model name and the API key of its provider, for example `LR_FRONTIER_MODEL=gpt-...` and `OPENAI_API_KEY`. Cloud calls cost money.
5. Optional: set `LR_DECIDE_MODEL` to a decision model, for example `ollama/d3:4b-q8_0`. It routes plain questions and reviews the words of each section.
6. Run `scripts/run.sh`.
7. Open `http://localhost:3100`. The API uses port 8100.

The first start generates the demo data in a few seconds. No data file is necessary.

## Demo datasets

Code in `living_reports/demos/` generates each one with DuckDB, with a fixed seed. Thus the data is the same on every machine. Each dataset covers January 2024 to December 2025. Each one has a semantic model with a better direction and targets, a set of eval questions with expected answers, and stories that a person can find with questions. Example: in the retail demo, "What is the Electronics gross margin by month?" shows the margin fall from about 28% to about 19% after July 2025.

| `LR_DEMO` | Audience | Tables | Stories in the data |
|---|---|---|---|
| `retail` | A retail commercial lead | `sales` (400,000 sale lines), `stores`, `products` | Online revenue grows 56% from 2024 to 2025, and Store revenue is flat. The Electronics margin falls after a discount push from July 2025. The Apparel return rate in the North is 19% in Q4 2025. November and December are the busiest months. Outlet stores miss the 35% margin target. |
| `hr` | An HR lead | `employee_months` (293,000 rows, one for each person and month), `employees` | Engineering Junior attrition triples after a reorganization in March 2025. Customer support absence and overtime rise in each quarter of 2025. Manchester misses the 1.2% monthly attrition target. New hires fall by 69% in the second half of 2025. People with less than 6 months of tenure leave three times as often. The HR data has no protected attributes. |

Each demo uses its own data directory, `.folio-data/<demo>`, so that reports, caches, and graphs do not mix. `LR_DEMO` selects the demo at startup. To change the demo while the app runs, open "Demo controls" on the home page and select a dataset in "Demo data". Folio prepares the data, the model, and the source of that demo, and then selects it.

The first selection of a demo generates its data in a few seconds. The change applies to all users of the server. Each demo keeps its reports, so they are there again when you select that demo again. A restart goes back to the demo in `LR_DEMO`.

If `LR_DATA_DIR` has a value, Folio uses it for every demo, and "Demo data" does not show.

Each demo has two moments. Moment 1 ends on November 30, 2025, and does not have one column. Moment 2 adds December 2025, that column, and one new value:

| Demo | New column | New value | Definition change |
|---|---|---|---|
| `retail` | `promotion_code` | channel "Social commerce" | Revenue: exclude returned sales |
| `hr` | `engagement_score` | leave reason "Redundancy" | Leavers: exclude retirements |

The unit tests use a small orders model: `LR_DEMO=orders` with the orders file in `LR_SOURCE_PATH`.

## How it works

1. **Understand the question.** Code finds the values, the period, and the count that the question names ("Online", "2024", "top 3"). A decision model picks the metric, the breakdown, and the time grain. A language model routes the questions that a choice cannot express. Each query uses only names from the semantic model.
2. **Get the numbers.** The query engine compiles the query to SQL and runs it on the source. Each value keeps its query, its metric version, and its data time.
3. **Compute the claims.** Code computes the claims that a section can make (value, compare, rank, share, extreme, change, trend, target, peak). It writes each claim from a template.
4. **Write and check.** The model writes its own sentences. Each number is a value token. Each finding word, such as "highest", "below", or "rose sharply", is a word token: code writes the word from a claim. The model can also cite a whole claim as a token. Code rejects a draft with a digit, or with a trend, rank, comparison, or cause word outside a token. A decision model then answers yes-or-no questions about the words. After 3 failed drafts, code writes the section from its top claims.
5. **Show the evidence.** The page shows each claim with its value and a citation number that opens the evidence.

The [reference](docs/reference.md) gives the detailed rules. The [design](docs/living-reports-design.md) explains the architecture. The [technical report](docs/papers/checked-narration.md) describes the narration patterns and the evaluation.

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `LR_DATA_DIR` | `.folio-data` | Application database (`app.sqlite`) and the DuckDB files of the sources (`sources/`) |
| `LR_DEMO` | `retail` | The demo dataset: `retail`, `hr`, or `orders` (the unit-test model). See "Demo datasets". |
| `LR_SOURCE_PATH` | None | The Parquet file of the `orders` demo. The generated demos do not use it. |
| `LR_SOURCES` | None | More sources, as JSON. See "Data sources" in the reference. |
| `LR_CARD_DATA_PATH` | None | A Parquet file of card transactions for the discovery acceptance test. Optional. |
| `LR_PG_DSN` | None | A Postgres DSN for `scripts/load_demo_postgres.py` and the live Postgres tests |
| `LR_PORT` | `8100` | API port |
| `LR_SMALL_MODEL` | None | Model for routing and narration |
| `LR_FRONTIER_MODEL` | None | Model for escalation. With no value, the ladder does not escalate. |
| `LR_CHECKER_MODEL` | None | Model for the wording challenge. Use a model that differs from the author model. |
| `LR_DECIDE_MODEL` | None | Decision model for the router checks. See "Routing" in the reference. A name that starts with `gemini/` (for example `gemini/gemini-decision-1`) uses the Gemini Decisions API with `GOOGLE_API_KEY`. |
| `LR_JUDGE_MODEL` | `LR_DECIDE_MODEL` | Decision model that judges the narration of each section |
| `LR_DECIDE_BASE_URL` | The Ollama server | Server with `/v1/systemone` |
| `LR_ROUTE_MODE` | `decision` | `decision`: the decision model routes a plain question, and the language model routes the rest. `model`: the language model routes every question. |
| `LR_DECIDE_CONFIDENT` | `0.8` | Probability at which a decision is confident |
| `LR_DECIDE_UNSURE` | `0.75` | Below this probability, the top metric is not clear |
| `LR_DECIDE_ANSWERABLE_FLOOR` | `0.3` | Below this probability, the question is not answerable |
| `LR_JUDGE_FLOOR` | `0.5` | Below this probability, the judge rejects a draft |
| `LR_NARRATE_ATTEMPTS` | `3` | Drafts the model gets before code writes the section. |
| `LR_RANK_FOLLOW_UPS` | `false` | Order the follow-up chips with the decision model |
| `LR_RANK_MODEL` | `LR_DECIDE_MODEL` | Decision model that orders the follow-up chips, for example `gemini/gemini-decision-1` while a local model routes |
| `LR_DEMO_MODE` | `true` | Allows the demo user header. Set `false` to reject it. |
| `LR_PUBLIC_URL` | `http://localhost:3100` | Link target in exported files |
| `LR_EXPORT_TEMPLATE` | `living_reports/web/dist-export/export.html` | Built template for exported files |
| `LR_LLM_PROVIDER` | None | Forces one provider: `anthropic`, `openai`, `google`, or `ollama` |
| `LR_OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama endpoint. `OLLAMA_HOST` also works. |

## Run the tests

```bash
.venv/bin/python -m pytest -q -m "not demo_data"
.venv/bin/ruff check living_reports tests
npm --prefix living_reports/web run test
cd living_reports/web && npx playwright test --config playwright.demo.config.ts
```

The Python and web tests use a stand-in for the model, so they test the code, not the quality of a model. The last command runs the demo test on the retail demo. The unit tests use a small orders model (`LR_DEMO=orders`). Some tests need a Postgres server (`LR_PG_DSN`) or a large data file (`LR_SOURCE_PATH`, `LR_CARD_DATA_PATH`). Without them, these tests skip. To measure a model on the eval questions, see "Measure a model" in the reference.

## Known limits

- **Generated data only.** Folio was evaluated only on generated data, with single runs. On the demo datasets, one run gave 92 correct answers for 94 questions. Section 9 of the technical report gives the details.
- **Probable is not proven.** Code checks each claim against the data. The words between the claims, and what the text leaves out, get only a model review or no review. The Evidence view shows which.
- **The facts depend on the definitions.** The checks prove that the text matches the facts. They do not prove that a metric definition is correct.
- **Demo permissions.** The `X-LR-User` header selects the user, so it gives no real security. Keep the API on the local host.
- **Single tenant.** Add a container or a microVM around the Deno process before any shared use.

The reference lists all known limits.

## License

Folio is licensed under the Apache License, Version 2.0. See `LICENSE`.
