# Folio reference

This document gives the detailed rules of Folio. For an overview and the quick start, read the [README](../README.md). For the design, read [living-reports-design.md](living-reports-design.md).

## Reports

A report is one HTML page with four views: Narrative, Dashboard, Decision brief, and Evidence. The model writes the sentences. Each number in them is a token that points to a traced value. Each finding word (a rank, a direction, a result against a target) comes from a claim that code computes, checks, and writes. Select a number to see where it comes from. Select a citation number to see the evidence for the claim. Each chart shows values on hover and on keyboard focus, and has a table view. You can download a published edition as one HTML file that works without the server.

Each total tile shows the change against the previous period and a sparkline of the last 12 months. For an unbounded query, the current period is the last 12 months and the previous period is the 12 months before. For a bounded query, the previous period has the same number of months before the start. Example: for "What was the revenue in 2025?", the tile shows `$20.9M` and the change against 2024, with the label `2025 vs 2024`. The chart kit has three more forms: small multiples, delta bars, and a tall segmented bar. A query by month and by a dimension with five or six members shows as small multiples.

You can also explore a report with no model call. Each report carries a cube: the metric components at the grain of the report dimensions and month. Use the filter row, or select a bar, to set one scope for the whole report. Drag across a line to select a range of months. Example: select Online, and the gross margin tile changes from `33.46%` to `33.38%`. The page calculates the margin as the gross profit of Online divided by the revenue of Online, not as a mean of rates.

The Exploring bar shows the scope with Reset and a Pin button that names the section, for example "Pin 'Channels' with this scope". If the report has more than one section, select the section first. Values under a scope are not traced. To trace them, pin the scope. A pin adds a draft section, so the report needs checks and signatures again. The exported file keeps the filters, the cross-filter, the brush, the compare tool, and a drill by the cube dimensions.

## Home

The home page has an ask box, a dataset panel, and four sections.

- **Dataset panel.** Select a dataset (a source) in the **Dataset** list. A question that you type goes to that dataset. Certified and suggested questions keep their own dataset. The panel shows the terms that you can ask about: the metrics, the dimensions ("By"), and the time field with its date range. Select a term to add its label to the question. Open a table to see its modeled columns. Each column shows its type, its count of distinct values, its share of nulls, and up to three example values. The panel shows only the columns in the model, because Folio can answer only about them. The data comes from `GET /api/sources/{source_id}/schema`.
- **Certified questions.** When an owner publishes a team or business-critical edition, each section of the edition becomes a certified question. A chip shows the question, a Verified badge, the owner, and the date. Select a chip to open the published edition. This answer makes no model call. The live report shows the Verified badge only while its content equals that edition.
- **Find a report.** Type a word to find reports by title or by the label of a number. The list shows at most 20 reports.
- **This week.** The weekly digest shows the top changes of the last seven days and suggested questions. Code builds the digest. It makes no model call.
- **What changed.** Each change card shows the word New for 24 hours. A resolved card stays for seven days and shows the word Resolved.

While Folio builds an answer, a progress panel shows four plain stages: "Understand your question", "Get the numbers", "Write the report and check each statement", and "Save the report". Each done stage shows its time. The panel does not name the internal steps or the models. A stage never goes back: a second draft or a try with the frontier tier stays in the stage that runs, so the panel shows each stage one time. The page sends a `progress_id` with the question and reads `GET /api/progress/{progress_id}`. The record is in memory only, and it stays for 15 minutes. The report page shows the same panel for a follow-up question.

Folio answers one question at a time for each user. While an answer runs, the page locks the ask box. If the same user sends the same question again (same source, report, and choice), the API refuses it with status 409 until the first answer ends. If a typed question already has a saved answer, the answer cache returns the same report. Thus a repeated question does not make a second report. A repeated follow-up question on a report still adds a new section.

The **Model** link opens the model page. If discovery or an import has proposals that wait for a person, the home page also shows the line "Review the model (N proposals)".

Demo controls are in one "Demo controls" disclosure at the bottom. They show only when `LR_DEMO_MODE` is `true`.

## Ask and edit a report

The draft page shows the routed question of each section as chips. Example: `Metric Revenue`, `By Channel`, `Region = East`, `Dates 2025`. Select a chip to change it. A chip change runs the section again with no model call. If a paragraph cites a value that the new query does not have, Folio removes that paragraph and tells you.

If Folio keeps a paragraph, its values can change under the words, so the page shows a note. In both cases, use "Regenerate wording" to write new words. A chip can change a filter with `=`, `≠`, or "is empty". A filter with another operator shows as a chip that you cannot edit.

Select a bar, a table cell, a month range, a number, or a section heading to point at it. The ask bar then names the selection, for example "Ask about Channel = Online…". The next question uses the selection as a filter. Folio adds the filter in code, so the model cannot drop it. You can always select a different member, in any chart or table, also after you explore a sub-segment. If a chart cannot apply the selection (for example, its cube does not carry that dimension), the chart shows all its data with the note "Not filtered" and the reason, and it stays selectable. If the report has no cube, a selection does not re-filter the charts, but the next question still uses it.

Under each section, follow-up chips suggest the next view. Code makes every candidate from the vocabulary: each dimension that the section does not use, "Over time", and the top member by each of those dimensions. Each candidate is a query that compiles against the semantic model. The section keeps the first three, in vocabulary order. With `LR_RANK_FOLLOW_UPS=true`, the decision model orders the candidates first, by which one most likely explains the section's top claim. The ranking is off by default. A section with a line also offers "Compare with previous period". Some sections filter to one member that the question names and break down by another dimension, such as the chip "Electronics by Brand". Such a section states that member's largest change first. A change in the wrong direction for its metric (a margin that falls, a return rate that rises) comes before a change in the right direction. The section's leading claim before this rule comes second. A follow-up adds a section with one narration call. An exported file has no follow-up chips.

Each section has a menu with these commands:

- **Shorter**, **Expand**, and **Regenerate wording**: one model call each. The words pass the same checks as narration.
- **Swap chart**: shows the forms that fit the query. No model call.
- **Edit wording**: opens the words in place. Values stay `{fact:id}` tokens. The server checks the text when you save.
- **Remove section**: asks you to confirm. The ids of the other sections do not change. Folio closes the threads on the removed section.

Under each chart, Move up, Move down, and Remove block change the order of the blocks. Remove block also asks you to confirm. Helpful and Not helpful record your feedback on a section. The feedback does not change the report.

Only an author can change the words: a user with the author role, or the author of the report. A contributor does not get this right.

These actions make you a contributor, and a contributor cannot sign the report: a chip change, a question or a follow-up on the report, Shorter, Expand, Regenerate wording, Edit wording, and Remove section. A chart swap, a block move, a block removal, and feedback change no value and no word, so they do not make you a contributor. Before an owner or a compliance user makes a first change that makes them a contributor, the page warns once: "Editing this means you cannot sign this report."

Each change returns the draft to the changed state, so the report needs "Run checks" again. The cost on the control rail is the total for all turns on the report. The export and the edition pages show none of these controls.

## Structured output

Each model call sends a JSON schema with the prompt. The Ollama and OpenAI backends use the schema to constrain decoding, so the model can only write valid JSON. The router schema also limits the IDs to the vocabulary of the semantic model: metric IDs, dimension names, and time grains. Thus the model cannot write an ID that does not exist.

The router writes the bare name of a dimension, such as `region`, and Folio changes it to the member ID, such as `stores.region`. If two datasets have a dimension with the same name, the router writes the member ID. The small tier gets four filter operators (`eq`, `ne`, `in`, `not_in`) and text values. The frontier tier gets all operators.

The router schema gives flat filters only. The query form also accepts nested `and` and `or` groups, but a grammar for a recursive schema is large. The API, code mode, and the UI can send groups.

A JSON schema forces valid JSON, but it does not force short JSON. Without limits, a small model can fill one field until it reaches the token cap. Thus `call_json` gives each array a `maxItems` of 12 and each free-text string a `maxLength` of 1,000 characters. It also limits each call to 2,048 output tokens. Ollama enforces these limits during decoding.

Keep each limit below 2,000. If a limit is 2,000 or more, llama.cpp rejects the schema with "failed to parse grammar", and each call fails. Thus `bound_schema` also reduces a larger limit that a model declares to 1,999.

The discovery schema takes its enums from the catalog of the source. Each dataset, column, and member that the model can write is an enum value. A column that Folio cannot read is in no enum. The enums have at most 20 datasets and 400 members, so a wide catalog also gives a small grammar. Each free-text string in this schema has a `maxLength` of 200 or less.

The Anthropic and Google backends ignore the schema. Folio validates every output and asks the model for a repair, so all backends stay safe.

A schema cannot make a model refuse. If a question has no matching metric, the schema forces the model to select a real metric or to give an `out_of_scope` reason. A relevance check is necessary for this case.

## Routing: decision model and judge

A decision model gives probabilities for a set of options. It does not write text. Folio uses one for three optional steps: routing, a check of the routed question, and the judge of the narration. Ollama 0.35 and later, and vLLM, serve these models on `POST /v1/systemone`. Set `LR_DECIDE_MODEL` to turn on the steps, for example `ollama/d3:4b-q8_0`. A model tag that ends in `mlx`, `mxfp8`, or `nvfp4`, and a model that has only these tags (such as `pplx-decider-flash`), runs only on Apple silicon. On other machines, use a `q8_0` tag. If the model is not set or does not answer, Folio uses the router alone. When the app starts, it loads the decision model and the judge in the background, because a cold load (about 75 seconds for `d3:4b-q8_0`) takes longer than a decision call can wait (10 seconds). If a call fails, Folio starts the load again for the next call. Thus a model that the server unloaded after 30 minutes with no use comes back.

**Decision-first routing.** With `LR_ROUTE_MODE=decision` (the default), the decision model routes a plain question, and the language model is not called for the route. One decision request asks four questions: is the question answerable, which metric, which breakdown ("none" or one dimension), and which grain ("none", day, week, month, quarter, or year). Code then adds the values, the period, and the count that the question names (step 1 and step 2 below). Example: for "Give me the top 3 brands by revenue.", the decision model gives revenue, brand, and no grain. Code adds the limit 3. The query is revenue by brand, the 3 largest. For "the 2 lowest regions", code sorts from the smallest value. If the question names one value of the breakdown dimension, such as "the Online channel", code makes it a filter with no breakdown. If the named period is not longer than the grain, such as "January 2025" by month, code drops the grain and shows one number. If the question has no time word, such as "month", "monthly", "trend", "over time", or "growing", code sets no grain. If the question asks how a value moves ("changing", "moving", "trending", "growing") and the decision model chooses no grain, the language model routes the question. A metric name in the plural, such as "return rates", names the metric.

The language model routes the question in these cases:

- The probability of an answer is below `LR_DECIDE_CONFIDENT`. Exception: if the two most probable metrics are close, Folio asks which metric the person means, with no language-model call.
- The question has a word that a choice cannot express: an exclusion ("excluding", "except", "without"), a threshold ("above", "more than", "at least"), "per", "ratio", or a request for rows ("list", "which orders").
- The question names two metrics, or two dimensions that it does not filter. Example: "Show revenue by region and channel."
- The question does not name a metric by its label or a synonym. The decision model always selects one metric, so a vague question such as "How is the business doing?" would get the nearest metric. A name inside a longer name does not count: "voluntary attrition rate" names one metric, not two.
- The query does not compile.

With `LR_ROUTE_MODE=model`, the language model routes every question. When the language model routes, each step below adds a check to its query. No step removes a choice that it made.

1. **Code finds the entities.** Before any model call, code finds four items in the question:
   - A value of a small dimension, as a whole word. Example: "Online" in "What is the return rate in the Online channel?" gives `channel = Online`. A value with a capital letter must match its case, and a capital at the start of a sentence does not count. Thus "New sales by region?" does not give `customer_segment = New`.
   - One explicit period: a year, a month and a year, a quarter, or "last month", "last quarter", "last year", or "this year". "Last" means the last complete period in the data. "This" means the calendar period that holds the last day of the data, to its end. Example: with data to 30 November 2025, "this year" is 1 January 2025 to 1 January 2026. Thus a certified question about "this year" counts the December rows when they arrive.
   - A start date: "after" or "since" before a period. "After March 2025" gives 1 April 2025. "Since March 2025" gives 1 March 2025. This is not the period "March 2025".
   - A count of items, such as "top 3".
2. **Code fills the gaps.** If the router query has no filter on that dimension, no time range, or no limit, Folio adds the item that the question names. The report shows an assumption for each added item. If the filled query does not compile, Folio keeps the router query.
   - If the router gives only one bound of the named period, Folio adds the other bound. Example: for "What was the revenue in 2024?", the router query `{"start": "2024-01-01"}` gets `"end": "2025-01-01"`. Without the end, the answer includes 2024 and all later data.
   - If the question has a start date and asks how a value moves ("change", "moving", "grew"), the series also shows the months before the start date. A change needs a "before" to compare with. Example: for "How did attrition for Junior engineers change after March 2025?", the router query `{"start": "2025-04-01", "grain": "month"}` loses its start. The report shows the assumption "The months before April 2025 too, to compare." Folio removes only a start that is the start date or the month before it. A question with no movement word keeps its start: "What was the revenue since March 2025?" counts from 1 March 2025.
   - If the router breaks down by a dimension and also filters that dimension to one value, Folio removes the breakdown and keeps the filter. Example: "gross margin by channel, where the channel is Online" gives one row. Folio shows the one number. A filter inside an "or" group does not count.
3. **The decision model checks the reading (first rung only).** It gets the question, and the metrics of the graph with their definitions and synonyms. It answers two questions: can the metrics answer the question, and which metric does the question ask about?
   - If the router gave a query and the decision model finds the question not answerable, the route carries a doubt. The ladder then asks the frontier model. With no frontier model, the answer is "out of scope", and the person can use Explore.
   - If the router refused and the decision model is confident of one metric, the ladder also asks the frontier model. With no frontier model, the refusal stays.
   - If the two most probable metrics are close and the router selected one of them, Folio asks which measure the person means. Example: if the router selects revenue for "How much did we make by channel?" and gross profit is close, Folio asks "Revenue by Channel" or "Gross profit by Channel".
   - If the decision model is confident of a metric that the router did not select, Folio asks the same question with the two readings.
   - If the question names no metric, Folio asks the same question with the router's metric and the most probable other metric of the decision model. A name, a synonym, or the last word of a name counts: "margin" names "Gross margin". Example: for "How is the business doing?", if the router selects revenue and the decision model prefers gross margin, Folio asks "Revenue" or "Gross margin". A refusal stays a refusal.
   - Folio compiles each option before it shows it. If the options give the same result, Folio uses the router query.
4. **The judge checks the narration.** After the code checks pass, the judge (`LR_JUDGE_MODEL`, default `LR_DECIDE_MODEL`) reads the text of the section with its data brief, and answers up to four yes-or-no questions (see "Claims and evidence", step 5). The judge does not read the title or the heading. A question below `LR_JUDGE_FLOOR` goes back to the writer as a repair note, the same as a failed code check. After `LR_NARRATE_ATTEMPTS` drafts, the ladder asks the next rung, or code writes the section.

The answer cache key includes the decision model, the route mode, the judge, and the four thresholds. Thus a change to one of these settings does not use an old answer.

## Claims and evidence

Code computes every statement of fact. The model selects the statements and writes the sentences around them. This is how a section is written:

1. **Code prepares a data brief.** The brief names what the query counted, in plain words. Example: "Revenue · Region is North · by Category and month · Jan 2024 – Nov 2025 · 104,290 sales". The brief holds a compact table of the result. A long series rolls up to quarters, half-years, or years, and the brief says so. A table that cannot be summed, such as a rate by region and month, keeps every dimension and shows the last periods. Nothing is cut without a note.
2. **Code computes the candidate claims.** A claim has one of nine kinds: value, compare, rank, share, extreme, change, trend, target, or peak. Code computes each claim from the facts and writes its text from a template. Example: "Store had the highest revenue of the 3 channels ($24.7M, 58.33% of $42.4M)". For a count, the words are "the most" and "the fewest": "Engineering had the most leavers of the 6 departments". A period that the data does not fill is named by its real span, for example "Jul–Nov 2025", and a trend never says "rose in every period" across it. Vague words have fixed meanings: "flat" is a change within 2%, "sharply" is a change of 20% or more. A trend names its flat steps: "Gross margin fell in 1 of 4 quarters and was flat (within 2%) in 3". A peak is one month at least twice the median of the other months, with each neighbor month at most half of it. The claim names the month and the ratio to the median month. A rate over more than 6 months gets its series by quarter. The query engine computes each quarter's rate from the rows, because a rate cannot be summed from its months. Code orders the claims by salience. If a question asks how a value moves ("changing", "grew", "trend") by a dimension over time, and names no member, the member with the largest change leads. Example: for "How is revenue changing by channel?", the first claim is "Revenue for Online rose sharply from $2.4M in 2024 H1 to $4.7M in 2025 H2 (+95.2%), a change for the better". The trend of the total comes second. The words "better" and "worse" come only from the metric (see "Direction and targets").
3. **The model writes a draft.** The model writes its own sentences with three kinds of tokens:
   - `{fact:id}`: a value. Code writes the number.
   - `{word:id}`: the finding word of a claim. Code writes it from the data: "highest", "2nd highest", "below", "rose sharply", or "went up and down". A value, a share, a comparison, and a peak have no word.
   - `{claim:id}`: the full sentence of a claim.

   Example (retail demo): the model writes "Electronics gross margin was {fact:…}, {word:…} the target of {fact:…}." The page shows "Electronics gross margin was 25.70%, below the target of 35.00%." The model cannot write a number or a finding word of its own. It can propose claims once for each section. It writes `focus`, up to 3 member labels from the table, and code gives each member the claims of a member that the question names: rank, share, and compare. These claims rank below the section's leading claims, so the two claims that every text states do not change. It writes `cross`, up to 2 comparisons with a member of an earlier section, and code computes each one only for the same metric over the same period, and each side names its section's filters, for example "West (all)" against "West (Web)". The model then writes the draft again with the new claims. The section stores `focus`, so a refresh computes the same claims again.
4. **Code checks the draft.** Each token must name a known claim. A sentence with a word token must hold one of the claim's own values (its value, the ends of its change, or a point of its trend), name the claim's members, and name no other member. "Not" or "never" cannot come just before a word token. Example: "West had the {word:…} fraud loss at {fact:…}", with South's value, is rejected. Outside the tokens, no digit and no word of trend, rank, comparison, judgment, or cause is permitted (for example "increasing", "highest", "more than", "worse", "missed the target", "because"). A title or a heading cannot hold a token. A recommendation must name the claims it rests on. It can use verbs such as "increase" and "raise", but not a digit or a word of cause. Code checks also keep the writing plain:
   - Two sentences cannot start with the same words (such as "In short,").
   - A section can cite at most 5 claims.
   - A sentence has at most 25 words. A claim, word, or value token counts as one word.
   - A sentence cites at most 2 claims. A word token cites its claim.
   - A short list of hard words gets plain words: "use", not "utilize"; "before", not "prior to"; "to", not "in order to"; "about", not "approximately".

   A rewrite (Shorter, Expand, Regenerate wording) gets the same three sentence rules. The prompt names the two most important claims of the section. If the draft leaves out one of them (as a word token or a claim token), code adds it at the start, one sentence for each claim.
5. **The judge checks what code cannot.** When a decision model is set, it answers four yes-or-no questions: do the model's own words add a finding that no claim states (such as "slightly" or "strong growth"), does the text answer the question, is an important claim missing, and does the recommendation follow from its claims. The judge reads the free text with each claim masked as "[cited claim]", each word as "[cited word]", and each value as "[value]". If the free words are only connecting words, such as "also" or "by contrast", code passes the first question without a model call. The section records which of these checks ran (see "Two levels of assurance").
6. **The loop ends.** The model gets `LR_NARRATE_ATTEMPTS` drafts (default 3). After that, code writes the section from the top claims, and the report says "Written by code". If a frontier model is set, the ladder asks it before code writes the section.

**Direction and targets.** A metric in the semantic model can state its better direction (`better`: `higher` or `lower`) and, for a rate or an average, a `target`. Code uses them in the claims, and the model cannot state them in its own words. Examples from the retail demo, where a higher gross margin is better (target 35%) and a lower return rate is better (target 8%):

- A change claim ends with "a change for the worse": "Gross margin fell from 34.24% in Q3 2024 to 31.05% in Q3 2025 (-9.3%), a change for the worse".
- For a rate or an average, a rank or an extreme claim at either end names the result: "Online had the highest return rate of the 3 channels (7.15%), the worst result". A sum or a count gets no "best" or "worst" between members, because a large member is mostly a big member.
- A target claim compares a value with the target: "Gross margin for Outlet was 25.60%, which misses the target of 35.00%", and "Return rate was 5.79%, which meets the target of 8.00%". If the value and the target round to the same text, the claim adds "(both round to" and the rounded value. A member that misses the target leads the section. A member that meets it gets a claim only when the question names it. The section total always gets one.

The target is a derived fact with the basis "the target in the metric definition", so the evidence shows it as a row next to the value. A target on a sum or a count is rejected, because a total depends on the length of the period. A metric with no `better` gets no judgment.

**Evidence.** Each claim ends with a citation number. Select it to open the evidence: the sentence and its verdict, what was counted, how the segment compares with its neighbors, how it changed over time, and how the metric is defined. The verdict is "Checked against the data" only when every value the claim cites is present. The Evidence view ("Claims and proof") lists each claim with its proof. The lineage of each value (metric version, data source, query) is under "For auditors". The Decision brief shows each recommendation with the claims behind it.

**Two levels of assurance.** The verdict of a claim covers the claim only. It does not cover the words around the claim, or what the text leaves out. The Evidence view shows "How each section was checked", with three lines for each section:

- **The numbers.** Code proves each claim. The line counts the checked claims and all claims, for example "Checked against the data (2 of 2 claims)".
- **The words between them.** One of four results: "Only connecting words, such as “also”. Checked by code.", "Reviewed by a second model. Probable, not proven.", "Fixed sentences that code wrote.", or "Not reviewed." The last result shows when no decision model is set.
- **What the text leaves out.** "Reviewed by a second model. Probable, not proven." or "Not reviewed." This check is never more than probable.

The evidence of each claim also says how the words around it were checked. Example: the claim "Online had the highest return rate of the 3 channels (7.15%), the worst result" is correct. A draft can put "Online customers are happy with their orders:" in front of it. The word guard does not list these words, so the draft passes the code checks. Only the second model can reject it. If it passes, the claim still shows "✓ Checked against the data", and the words show "Reviewed by a second model. Probable, not proven." With no decision model, the words show "Not reviewed."

The section stores the record in `review`. The name of the model that reviewed the words shows only under "For auditors".

**Refresh.** After new data loads, code checks every claim again. A claim that the new data does not support is marked "No longer true with the new data", and the section offers "Rewrite this section". Folio does not rewrite text on its own. If the new data changes a value that a second model read, the words show "Reviewed by a second model before the data changed. Not reviewed since." A chip change that changes a value does the same. A rewrite reviews the words again.

**Limits.** A member with a negative value makes the shares of the other members exceed 100%. The word guard can reject a name that holds a listed word, such as "Greater London" or "this fall"; write the sentence in other words. The checks guarantee that the text matches the facts. They do not guarantee the facts: those depend on the metric definitions and the query engine. A statement of fact in words that the guard does not list reaches only the judge. With no judge, nothing checks it, and the report says "Not reviewed". A true claim can still mislead by what the text leaves out; the judge's question about a missing claim is probabilistic. A recommendation is an opinion; only the claims behind it are checked.

**Dates.** The query engine reads the end of a date range as exclusive. An end on the last day of a month, or on the last day of the data, becomes the next day. Thus the last day stays in the result. This also applies to an end that a person meant as exclusive: write the first day of the next month for that case. Every display shows the inclusive last month: "Jan 2024 – Nov 2025". Values show as labels, for example "Jan 2024" for a month.

## Data sources

Folio reads analytical data only through a source. A source runs one read-only `SELECT` at a time. It returns at most 1,000 rows, and it stops a query after 20 s. Each value records its source and the data time of the source.

- **The demo source.** `duckdb_demo` is a DuckDB file in `<LR_DATA_DIR>/sources/`. At startup, the API builds it from the demo data in `<LR_DATA_DIR>/demo/<demo>/`. If the data is not there, or a generator has a new version, the API generates it first. The demo has two moments (see "Demo datasets" in the README). "Load the next data snapshot" in the demo controls rebuilds the file at moment 2.
- **DuckDB sources.** A DuckDB source opens a database file read-only, with no access to other files or to the network. A `files` source copies Parquet or CSV files into one DuckDB file.
- **Postgres sources.** A Postgres source opens one read-only connection for each query and sets a statement timeout. Install the driver with the `postgres` extra: `uv pip install --python .venv/bin/python -e ".[dev,postgres]" -c constraints.txt`.

Example of `LR_SOURCES`:

```json
{"warehouse": {"kind": "postgres", "dsn": "postgresql://folio@db.example.com:5432/analytics"},
 "shop": {"kind": "duckdb", "files": {"sales": "data/sales.parquet"}}}
```

`GET /api/health` checks each source. If a source cannot answer, the health check answers 503 and gives a short reason without the DSN. A question on that source fails with the message "The data source warehouse is not available."

With `LR_DEMO=orders` and no `LR_SOURCE_PATH`, the API seeds no demo model. The model then comes from discovery or from an import (section "The model page").

To load the active demo into Postgres, run `.venv/bin/python scripts/load_demo_postgres.py`. The script reads the DSN from `LR_PG_DSN`. It writes the tables of the demo at moment 2. The `orders` demo needs `--source <orders file>`.

## The model page

The model page (`/model`) turns a source into a semantic model. A person confirms each item. No seed code is necessary. Only an owner can run discovery, import, confirm, rename, reject, or certify.

### Connect a source and run discovery

1. Add the source to `LR_SOURCES`. Example: `{"shop": {"kind": "duckdb", "files": {"sales": "data/sales.parquet"}}}`.
2. Start Folio and open `http://localhost:3100/model`.
3. Select the source, and then select **Run discovery**.
4. Read the probe of each item.
5. Select **Confirm** for one item, or **Confirm all**.

Discovery reads the catalog of the source: tables, columns, types, declared keys, row counts, distinct counts, null rates, and three sample values for each column. The model gets the catalog, never the data. The model proposes datasets, the time field, dimensions, relationships, and up to 12 typed metrics. Folio then checks each item in code:

- A relationship gets a uniqueness probe on its one side. Example: "stores.store_id is unique over 120 rows". If a key has duplicates, Folio drops the relationship and gives the count of duplicate keys.
- A metric compiles through the query engine and runs once with a limit of one row. The probe shows the metric label and the value. If the metric needs a dropped relationship, Folio drops the metric too.
- A column with a type that Folio cannot read (for example `BLOB` or `TIME`) is not proposed. The run lists it as dropped with the reason "unsupported type BLOB".

Each item that passes becomes a proposal. Nothing changes in the model until a person confirms it. **Confirm all** confirms in this order: sources, datasets, fields, relationships, metrics. If you confirm one metric before its field, the page shows "Confirm sales.revenue first" and writes nothing. **Rename** changes the label of a field or a metric. The id does not change. The person who renames an item becomes its proposer, so another owner must confirm it. The page confirms only proposals from discovery or an import.

Discovery never moves a dataset of another source. If `sales` belongs to `duckdb_demo`, a run on a second source drops `sales` and each item that uses it, with the reason "sales belongs to source duckdb_demo".

If you run discovery again, Folio skips an item when the model already has the same definition. It also skips an item that a pending proposal already holds. Folio proposes a rejected item again.

### Model files (Ossie)

Folio exports the confirmed model as an Apache Ossie document, version 0.2.0.dev0. Select **Export** on the model page, or call `GET /api/model/export`. Each metric has its SQL text, so other tools can read it. Folio keeps its typed metric (measure, label, format, and certification) in a `folio` entry of `custom_extensions`.

To import a document, open **Import** on the model page, paste the document, and select **Import document**. An import is one more source of proposals: Folio checks it, probes it, and a person confirms it. The import has these rules:

- The document is at most 1 MB and at most 32 levels deep. Its `version` must start with `0.2`. YAML anchors and aliases are not accepted.
- The person who imports a document is the proposer of its items, so another owner confirms them.
- A relationship must join columns of its two datasets. Two datasets cannot read the same table.
- A metric with only SQL text must be one aggregate over one dataset, with no subquery. Example: `sum(sales.revenue) filter (where sales.channel <> 'Online')`. Folio drops other metrics with the reason.
- An imported SQL metric stays uncertified, also when the document says that it is certified. An owner certifies it with `POST /api/model/metrics/{id}/certify` after a review of the SQL text.
- A computed field must be one row expression over the columns of its dataset, with no aggregate and no subquery.

## Demo users

The demo has seeded users. The user selector is in the "Demo controls" disclosure on the home page and in the control rail of a report. It shows only when `LR_DEMO_MODE` is `true`. Folio has no real identity system.

| User | Role |
|---|---|
| `ana` | Author |
| `omar` | Owner |
| `priya` | Owner |
| `chidi` | Compliance |

The demo has two owners because of the four-eyes rule: the person who proposes a model change cannot confirm it. For example, if `omar` runs discovery, `priya` confirms the proposals.

## Measure a model

Measure a model:

```bash
LR_SMALL_MODEL=ollama/qwen2.5-coder:1.5b .venv/bin/python -m living_reports.evals.run --tier small --runs 3 --out results.json
```

The evaluator uses the demo source at moment 2 and the eval questions of the demo that `LR_DEMO` names (`living_reports/evals/questions_<demo>.yaml`). Add `--ladder` to measure the escalation ladder. Add `--limit 6` to use every sixth question as a sample. To measure on another source, add `--source <id>`. To seed the demo model on that source, add `--seed-source <id> --seed-dialect postgres --seed-schema public`.

The result has a `narration` summary: the sections that the model wrote, the sections that code wrote (`fallback_rate`), the drafts per section, the first drafts that passed, and the count of each draft outcome (`passed`, `code_check`, `judge`, `invalid_json`). Each item in `answers` keeps, for each draft, its outcome, its problems, and its readable text. The `routing` summary counts the answers that the decision model routed and the answers that the language model routed, and the correct answers of each.

An OpenAI reasoning model (`gpt-5`, `o1`, `o3`, `o4` names) gets `max_completion_tokens` and minimal reasoning effort, and no temperature. A `gpt-6` model gets `max_completion_tokens`, the reasoning effort `none`, and the temperature.

## Known limits

- **Accuracy is measured only on generated data, with single runs.** On the three demo datasets (94 questions), with a small hosted model as the writer and `d3:4b-q8_0` as the decision model, one run gave 92 correct answers. The decision model routed 61 questions, and all 61 were correct. Two misses remain: Folio refuses "Show revenue by category for 2025.", and it gives a wrong query for the monthly attrition rate of one job level in one department. No traced value was wrong in any run. The evals measure the routed query, not the quality of the narration. Section 9 of the technical report (`docs/papers/checked-narration.md`) gives the details. The older results are in `docs/living-reports-evals/`.
- **A model can answer with the wrong metric.** For "How many employees work in each region?", the small model returned order counts with an "Employee" title. The 7B model asked a clarifying question instead. A decision model (`LR_DECIDE_MODEL`) adds a relevance check, but its probabilities are not calibrated. Tune the thresholds with the evals before real use.
- **The 2B decision model is not reliable enough.** With `d3:2b-q8_0`, the probability that a question is answerable is below 0.2 for some real questions, for example 0.04 for "Break down orders by region." Folio then refuses the question. Use a 4B or larger decision model.
- **A word token proves the word, not the sentence.** Code proves the word, its value, and its member. In "North had the highest return rate at 6.30%, only slightly above South", only the judge reads "only slightly". A sentence with two word tokens can swap them between its two members.
- **The judge does not see the title or the heading.** Thus a wrong claim in a title, such as "Online leads revenue", gets no judge check. In a test, a 4B judge read plain labels such as "Trend over time." as stated facts and sent correct sections back to the writer.
- **A certified question can become a draft again.** If a definition that the question uses changes, the question is not current. The chip says "Definitions changed since". Then a selection asks the question again with one narration call. The new report is traced, but it is not verified until an owner publishes it.
- **A source without as-of reads cannot split a change.** Plain Postgres and DuckDB files have no as-of reads. Thus Folio cannot split a difference between two editions into new data and a new definition.
- **A Postgres source never uses the result cache.** Its data time is the server time, so each query runs again.
- **Conversational data connection is not built.** A person cannot connect a new data file through the conversation. Use the model page ("The model page").
- **Deployment is single-tenant.** Add a container or a microVM around the Deno process before any shared deployment.
- **Any user can close a thread.** The API rejects a second close (409) and a decision that states a value (422). It does not limit who can close a thread.
- **Permissions are for a demo.** The `X-LR-User` header selects the user, so it gives no real security. Keep the API on the local host. If `LR_DEMO_MODE` is `false`, the API rejects all data requests until a real identity provider exists.
- **Folio cannot explore some reports.** A report has no cube if it has more than 5,000 cube cells. A date bound that is not on day 1 of a month also stops the cube. A top query, such as the top 3 channels, stops the cube if the report has other queries. The cube reads only the rows of the widest report query, so it never shows rows that the report does not cover. It also reads one previous period of the same length, for the change and the compare tool. For a top query, it reads only the members that the query shows. If no single query covers all the others, the report has no cube. The filter row then says that exploration is off. Only a sum, a count, an average, or a ratio of two sums or counts can be re-aggregated. A metric on a dataset with no time field stops the cube, because the cube is monthly. For any other metric, the chart keeps its own data under a selection, with the note "Not filtered" and the reason.
- **A chip changes only simple filters.** The chips show and change filters with one member and one operator. A filter group (and, or) stays in the query, but no chip shows it.
- **Refreshed text can become stale.** A refresh updates values and charts, but it keeps the narration. The wording challenge reviews this text. The Evidence view says when a review of the words is older than the data.
