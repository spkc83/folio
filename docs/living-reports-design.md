# Folio: design

Folio is an educational project, not a product. It shows how a governed, conversational reporting tool can work.

## 1. Intent

Folio replaces manual reporting setup with conversation. A person asks a business question. Folio answers with a governed, interactive HTML report. The report then changes when the data and the domain change.

Folio has two purposes:

- Show that conversation can replace report configuration.
- Show that careful engineering can reduce the operating cost of each correct answer.

The UX matters most. Folio must be clean, neat, professional, simple, easy to use, and pleasant to look at.

## 2. Goals, non-goals, and anti-goals

### Goals

- Answer questions from the objective of the question, not from a user persona.
- Ask a clarifying question only when the answer changes the result.
- Show one report in four views, with the same numbers in each view.
- Keep each published edition frozen and traceable.
- Find changes in data and definitions, and propose updates.
- Run with small models and frontier models.
- Show the cost of each answer on screen.

### Non-goals for the MVP

- Live warehouse connections in production. The MVP reads a DuckDB demo source. `PostgresSource` shows that the query path works on a second database.
- Multi-tenant deployment.
- A full identity system. The demo uses seeded users and roles.

### Anti-goals

Folio fails, even if it works technically, in these conditions:

- A number changes meaning with no record and no approval.
- The demo uses scripted answers or hard-coded charts.
- The UI looks like a chat window with charts attached.
- The checker lets a person approve their own report.
- Unreviewed facts from conversation change a business-critical report.

## 3. The demo

The demo has generated datasets for two audiences: retail sales and HR. `LR_DEMO` selects one at startup, and the "Demo data" control on the home page selects another one while the server runs. Each dataset has its own data directory, so a change of dataset keeps the reports of each one. Code in `living_reports/demos/` generates each dataset with DuckDB from a fixed seed, so the data is the same on every machine and needs no file outside the repository. Each dataset covers January 2024 to December 2025 and has 290,000 to 400,000 fact rows. All values come from real queries on the generated data.

Each dataset has planted stories that a person can find with questions in the vocabulary. Tests hold each story to a range. Example: in the retail data, the Electronics gross margin falls from 28.2% to 19.2% after a discount push from July 2025. The README lists the stories of each dataset.

Each dataset supplies all three change events:

| Event | Retail | HR |
|---|---|---|
| New data arrives | December 2025, with the new channel "Social commerce" | December 2025, with the new leave reason "Redundancy" |
| New column appears | `promotion_code` | `engagement_score` |
| A metric definition changes | Revenue excludes returned sales: $46.0M becomes $43.0M | Leavers exclude retirements |

The demo has one source, `duckdb_demo`: a DuckDB file that a build job writes from the generated Parquet files. The demo has two moments. Moment 1 contains the rows to Nov 30, 2025, without the new column. Moment 2 contains all rows. "Load the next data snapshot" rebuilds the same file at moment 2. The source stays the same, and its data time changes.

Each dataset has a fact table and one or two dimension tables, joined many to one. A build fails with a sentence if a key repeats in a table, because the join would then count a fact twice. Thus the demo exercises real joins. Each demo keeps its own data directory, so the reports and the graph of one demo never mix with another.

The demo script has eight steps:

1. Ask a question, for example "Which categories drive the most revenue?" on the retail demo. Folio opens a Narrative report.
2. Ask a follow-up on a number. The thread stays on that number.
3. Ask, "How are we doing by channel?" Folio opens the Dashboard view of the same data.
4. Publish edition 1 as a team report. The checks run and the owner signs.
5. Load the next data snapshot (moment 2). The home page shows the new value and the new column.
6. Approve the changed definition. Folio creates edition 2 and shows the differences.
7. Mark the report as business critical. The checks run. The owner signs, and then the compliance user signs.
8. Open the Evidence view and trace one number to its query.

An optional step connects a new data file through conversation. Folio then produces reports on that data with no configuration. Folio does not do this step yet.

## 4. How Folio works

### Four stages

| Stage | Contents | Mutable |
|---|---|---|
| Definition | Questions, metric links, layout, writing rules | Yes, with versions |
| Live draft | The definition with current data, labeled Draft | Yes |
| Checks | Results of the automated review | Stored with each run |
| Edition | Values, query hashes, data time, check results, signatures | No |

Publishing a report means creating an edition. An edition link always shows the same content. A separate "latest" link shows the newest edition.

### Tiers

The tier comes from the content and the audience of the report.

| Tier | Requirements to publish | New data |
|---|---|---|
| Exploratory | None. The page shows an Unverified label. | Updates live |
| Team | Checks pass, and the owner publishes | Creates a new edition automatically |
| Business critical | Checks pass, and the owner and compliance sign | Needs new signatures |

Signatures bind to the hash of the edition. Any change creates a new hash, so the old signatures do not apply to the new edition.

### The change rule

Folio can add a chart, a breakdown, or a view without approval on a draft. A change to the meaning of a metric needs one human approval. The graph records this approval. Folio then lists the affected reports.

## 5. Experience

### Four views of one report

The system selects the view from the objective of the question. The reader can change the view at any time.

| View | Objective |
|---|---|
| Narrative | Why did this happen? |
| Dashboard | How are we doing, and where should I look? |
| Decision brief | What do I need to decide? |
| Evidence | Can I trust this number? |

### Features of every report

- **Provenance on each number.** A person selects a number, and a card shows the metric, its version, its definition, the data, the query, and the check result. The card has the button that opens a question on that number.
- **Anchored questions.** A person asks from the card of a number, or about a section. The thread stays attached to that place.
- **A state badge.** The top bar shows one word with an icon: Certified (published edition), Traced (draft with checks), or Unverified (exploratory).
- **Charts that a person can read without a pointer.** Each bar and each line point shows its value on hover and on keyboard focus. Each chart has a Table button that shows the same values as a table.
- **Tiles with a change and a trend.** A total tile shows the change against the previous period of the same length, and a sparkline of the last 12 months. A rate shows its change in points, and other metrics show a relative change.
- **Deterministic exploration.** One scope applies to the whole report. A filter row, a click on a bar, or a drag across a line sets it. The page re-aggregates the report cube in the browser, with no model call. A ratio such as the gross margin is the sum of its numerator divided by the sum of its denominator. The Exploring bar shows the scope. Values under a scope are not traced until a person pins the scope as a draft section. A report has no cube when its cube needs more than 5,000 cells. The page then does not re-aggregate. A click still selects a member, the Exploring bar shows the selection, and the next question uses it. A chart that cannot apply the scope keeps its own data and shows "Not filtered." with the reason. A click on another member of any chart replaces the selection.
- **The routed question as chips.** Each section of a draft shows its query as chips, for example "Revenue · by Channel · Region = East · 2025". A chip change runs that section again with no model call. A clarifying question shows each choice as a chip row.
- **Point to select.** A person selects a bar, a table cell, a month range, a number, or a section heading. The ask bar names the selection, and the next question uses it as a filter.
- **Follow-up chips.** Code makes every candidate from the vocabulary, and each candidate is a query that compiles. The section stores the first three, in vocabulary order. With `LR_RANK_FOLLOW_UPS=true`, the decision model orders them first, by which one most likely explains the section's top claim (`harness/followups.py`). The model never writes a chip. Some sections filter to one named member and break down by another dimension ("Electronics by Brand"). Such a section leads with that member's largest change (`_lead_move` in `harness/claims.py`). A change in the wrong direction for its metric comes first.
- **Section commands.** Shorter, Expand, Regenerate wording, Swap chart, Edit wording, and Remove section. Only the three wording commands call the model, one call each.
- **Editing by people.** An author edits the words in place. Values stay tokens. Move up, Move down, and Remove block change the order of the blocks. Feedback per section goes to the usage ledger and does not change the report.
- **Control rail.** A slim panel shows the edition, the check results, the signatures, and the cost.
- **Change briefing.** The home page shows what changed since the last visit. Section "Home" gives the rules.
- **Stated assumptions.** The page shows each assumption, and the reader can change it.

### Report pages

A report opens as a separate HTML page in a new tab. The page keeps its threads, so a question continues the same work.

| | Hosted page | Exported file |
|---|---|---|
| Questions | Available | Link back to the hosted page |
| Permissions | The server enforces them | Not enforceable after sharing |
| Data | Fetched as needed | Aggregates only, never raw rows |
| Exploration | Filters, cross-filter, brush, compare, drill, and pin | Filters, cross-filter, brush, compare, and drill by the cube dimensions. No pin. |

The LLM never writes page code. It writes a declarative specification, and one fixed renderer creates the HTML. Hosted pages use a strict content security policy.

### Home

The certified question set is the durable asset of Folio. Each row is one pair: the intent of a section and the exact query of that section.

- **Who writes it.** `Signoff.publish` writes the rows in the same transaction that publishes the edition. The publisher is the verifier, and the publish time is the verification time. The key of a row is the question, the report, and the edition. Thus a later publish adds a row and keeps the earlier rows as history. The home page shows the newest row for each question. An exploratory edition writes no rows.
- **When a question is current.** Each row keeps a hash of the definitions that its query reads. Folio compiles the query and takes these definitions from the plan: the metrics, the members that the query groups, filters, or measures by, their datasets, and each relationship that the query joins through. Thus a change to `sales_stores` makes a question by `stores.region` not current, but a question by `sales.channel` stays current. A row from before the query engine holds the old query form, and Folio reads it in the new form. The hash includes the definition text. It excludes labels, synonyms, and node versions, because these do not change a value. Folio takes the hash from the graph as the edition saw it. A question is current while the same hash comes from the live graph. A new report or another metric does not change the hash. If a metric or a member is retired, Folio skips the question, and a request for it fails with a message.
- **The certified edition.** Each row keeps the number and the content hash of the published edition. The certification applies to that edition only. The live report can change after publish, for example after a refresh or a follow-up question. Then the live report has no Verified badge. Only a certified edition shows the badge. An edition with several sections shows "Verified; definitions changed since" if one of its sections is not current.
- **The zero-cost rule.** If a question is current and its edition still has the certified content, a selection opens that edition with no model call. The cost line says "certified question". If the question is not current, Folio asks it again with one narration call. The new report is traced, but it has no Verified badge.
- **Moved values.** After new data or an approved definition change, Folio runs the total of each certified question again. If a total moves, the feed gets a change item of the kind `question`, for example "Revenue: $42.4M → $46.0M". Each question has one open item. A newer item closes the older item, and a new publish of the question closes the open item.
- **The digest.** `GET /api/digest` uses the last seven days. It ranks changes by kind (definition, certified question, new data, new value, new column), then by the number of affected reports. Suggested questions come from certified questions and from new values with the first metric of the vocabulary, for example "Average sale value for Social commerce". Code builds the digest with no model call.
- **New and resolved.** A change item is new for 24 hours after Folio detects it. A done item stays in the feed for seven days.
- **Search.** One SQL statement searches report titles and fact labels. Folio escapes the `%`, `_`, and `\` characters, so they match only themselves.

### Claims and evidence

Code computes each statement of fact. The model selects the statements and writes the sentences around them. For each section, code builds a data brief: what the query counted, a compact table, and the candidate claims. The model writes its own sentences. A value is a `{fact:id}` token, the finding word of a claim is a `{word:id}` token ("highest", "below", "rose sharply"), and a full claim is a `{claim:id}` token. Example: "Electronics gross margin was {fact:…}, {word:…} the target of {fact:…}." shows as "Electronics gross margin was 25.70%, below the target of 35.00%." Code checks the draft against six rules:

- Each token names a known claim.
- A sentence with a word token holds one of the claim's own values, names the claim's members and no other member, and does not negate the word.
- Outside a token, the text has no digit and no word of trend, rank, comparison, judgment ("worse", "missed the target"), or cause.
- A title or a heading has no token.
- A recommendation names the claims that it rests on.
- No two sentences start with the same words, and a section cites at most 5 claims.

The claims use the business fields of a metric. `better` (`higher` or `lower`) adds "a change for the worse" to a change, "each a change for the better" to a trend that moves one way, and "the best result" or "the worst result" to a rank or an extreme at either end of a rate or an average. A sum or a count gets no "best" or "worst" between members: a large member is mostly a big member. `target` makes a target claim: "Gross margin for Outlet was 25.60%, which misses the target of 35.00%". A miss leads the section. The target is a derived fact, so the evidence shows it next to the value.

A decision model judges what code cannot check: whether the model's own words add a finding that no claim states, whether the text answers the question, and whether it leaves out an important claim. After `narrate_attempts` drafts, the ladder asks the frontier model when one is set. When the last rung also fails, code writes the section, and the report says so. Each claim keeps its evidence: what was counted, the comparison rows, the series, and the definition. The page shows the evidence from a citation number. After new data loads, code checks each claim again. It marks each claim that the new data does not support.

The report shows two levels of assurance apart. Code proves each claim. The words between the claims, and what the text leaves out, get a weaker check. Each section stores a `review`: the words were only connecting words (checked by code), reviewed by a decision model (probable, not proven), written by code, or not reviewed. It also records whether a decision model checked for a left-out claim. The Evidence view shows these three lines for each section, and the evidence of a claim names the check of the words around it. A refresh or a chip change that moves a value that the model read marks the review as older than the data.

The checks guarantee that the text matches the facts. They do not guarantee the facts, which depend on the definitions and the query engine.

### Visual direction

The direction is "Winter Chill": a cool, quiet surface that keeps the attention on the numbers. The tokens are in `living_reports/web/src/theme.css`.

- Use one sans-serif face (Inter) for headlines, report text, labels, controls, and tables.
- Use a frost paper background (`#f2f6f7`) with white, soft rounded cards. Use deep winter-teal ink (`#0b2e33`).
- Use one slate-teal accent (`#2f6a75`; ice `#8ecbd5` in dark mode). Save it for interactive items.
- Dark mode has its own surfaces (`#0b1d21` paper, `#10282d` card), not an inverted light mode.
- Use the six categorical colors in a fixed order for more than one series. A member keeps its color when a filter removes other members. Light and dark mode each have their own validated set.
- Use the sans-serif face for large values in tiles. Use tabular numerals in tables and on axes.
- Give each chart a zero baseline, a y-axis with three to five round ticks, and a label on the last point of each line.
- Use small multiples when a line has five or six members. The panels share one y-axis and one crosshair.
- Use delta bars for the change of each member. The axis is signed, it has a zero line, and the sign is in the text. An increase uses the first slot color, and a decrease uses the second.
- Use the tall segmented bar for parts of a whole. It shows the share inside a part only when the share is 8% or more. A rate cannot use this form.
- Show the change in a tile in ink, not in color. The graph does not yet say whether a higher value is good.
- Give each view generous white space.

The innovation is in behavior, not decoration. The three key moments are asking on a number, seeing the view match the question, and seeing a report update with a clear record of the change.

## 6. Context and memory

Folio has no separate memory system. Four small stores supply all context.

| Store | Contents | Loaded each turn |
|---|---|---|
| Report definition | Intent, assumptions, and decisions for each section | Only the relevant section |
| Context graph | Metrics (with their better direction and target), joins, definitions, report lineage | Only the linked nodes, at the data time of the report |
| Edition archive | Frozen reports | Only by ID |
| Conversation log | The raw transcript, for audit | Never as a whole |

A closed thread becomes a short decision entry on its section. Each entry keeps an ID that points to the full thread.

## 7. Context graph

The graph is bi-temporal. It records when a fact was true and when the system learned it.

For example, assume that a definition changes on July 1 and a correction arrives on July 20. The graph can then answer three questions about June:

- What is June under the current rule?
- What was June under the rule in force in June?
- What did the report show on July 10?

### Graph, ontology, and semantic layer

The context graph stores the ontology and the semantic definitions. The query path does not read the graph directly. It reads a typed semantic model that code projects from the graph. Three parts do this work:

| Part | Code | Function |
|---|---|---|
| Graph engine | `folio_graph/` | Stores typed nodes and edges as versions. It knows no Folio terms. |
| Ontology | `living_reports/semantic/kinds.py` | Declares the kinds of node, their payload models, and the edge rules. |
| Semantic layer | `semantic/projections.py` and `engine/` | Projects a slice into a `SemanticModel`. The engine compiles each query from that model. |

Example on the demo data:

1. The graph holds the nodes `sales` (a dataset), `stores.region` (a field), `sales_stores` (a relationship), and `revenue` (a metric: the sum of `sales.revenue`). The ontology allows these kinds and their edges.
2. A question asks for revenue by region. Code takes a slice of the graph at the data time and calls `semantic_model(slice)`.
3. The `SemanticModel` tells the engine that `revenue` sums `sales.revenue` and that `region` is on `stores`, through the join `sales_stores`. The engine compiles the SQL from this model.
4. A second projection, `vocabulary(slice)`, gives the metric and dimension terms. The router schema and the UI use these terms.

A metric node also holds business knowledge: its better direction and, for a rate or an average, its target. The claims use these fields (see "Claims and evidence").

The ontology is light. It is a set of typed kinds with edge rules, not a formal ontology language such as OWL. It has no class hierarchy and no inference.

The graph also holds lineage: `Report`, `Edition`, and `Thread` nodes, and an edge from each edition to each metric that it cites. With these edges, Folio finds the reports that a definition change affects, and marks a certified question "Definitions changed since".

### Engine

The package `folio_graph` is the graph engine. It knows no Folio terms. Each kind declares a Pydantic payload model and its edge rules. The engine rejects a payload that does not validate and an edge that no rule allows. A change starts as a proposal with the status `candidate`. A confirm writes a new version. The engine never overwrites a row.

A reader takes a slice with `as_of(valid_at, known_at)`. The slice has a version stamp: the number of confirmed rows recorded up to `known_at`. Each write adds at least one row, so the stamp grows with each confirm, retire, and supersede.

### Folio kinds

The package `living_reports.semantic` registers eight kinds in three layers:

| Layer | Kinds | Node id |
|---|---|---|
| Physical | `Source`, `Dataset`, `Field`, `Relationship` | `duckdb_demo`, `sales`, `stores.region`, `sales_stores` |
| Semantic | `MetricBinding` | `revenue` |
| Report | `Report`, `Edition`, `Thread` | `report:{id}`, `edition:{id}:{number}`, `thread:{id}` |

A metric is typed. It has one form: a `measure` (an aggregation of a field, with an optional typed filter), a `ratio` of two metrics, or an `expression`. No payload holds SQL text, except a row-level field expression and the display text of an expression metric. Thus the API rejects a proposal that sends the old `expr` attribute (status 422).

A metric can also hold business knowledge. `better` is `higher`, `lower`, or empty: in the retail demo, a higher revenue and gross margin are better, and a lower return rate is better. `target` is a value for a rate or an average, such as 0.35 for `gross_margin` and 0.08 for `return_rate`. A target needs `better`. The payload model rejects a target on a `sum`, `count`, or `count_distinct` measure, because a total depends on the length of the period. The claims read these fields (see "Claims and evidence"). Ossie keeps them in the `folio` extension when they are set.

Each name that can reach SQL must be a plain identifier, such as `gross_profit` or `stores.region`. The payload models reject other names. A dataset can have no primary key when the source declares none. Over the API, a person can propose only a `MetricBinding` or a `Field` with no expression. The API rejects other proposals (status 422). If a proposed metric has an expression, the server parses its SQL text with sqlglot and stores that AST. The server ignores an AST from the client. If the text breaks a rule, the API rejects the proposal with a sentence (status 422).

The retail model has three datasets. `sales` (table `main.sales`, time field `sale_date`) holds the sale lines and the metrics, such as `revenue`, `gross_margin`, and `return_rate`. `stores` holds `store_id` and the dimensions `region` and `store_format`. `products` holds `product_id` and the dimensions `category` and `brand`. The relationships `sales_stores` and `sales_products` join `sales` to `stores` and to `products`. The unit tests use a smaller orders model (`LR_DEMO=orders`) with the same shape: one fact dataset and one joined dataset.

Some edges follow from a payload. When a person confirms a node, the graph writes these edges. When a new version names a different target, the graph retires the old edge. For example, the metric `revenue` measures `sales.revenue`, and that field belongs to `sales`. Thus `dependents("sales")` finds the field, the metric, and each report that uses the metric. A `Relationship` node also writes a `joins_to` edge between its two datasets.

### Projections

Consumers read projections, not raw nodes:

- `SemanticModel`: the datasets, fields, relationships, and metrics in a slice. Its `model_version` is the slice stamp. If a metric refers to a field that is not in the slice, the projection raises an error that names the metric and the field.
- `Vocabulary`: the metric terms, the dimension terms, the time fields, the filter operators, and the time grains. The UI and the language model use these terms.

### Discovery

Discovery replaces seed code with a conversation. The package `living_reports/discovery/` turns the catalog of a source into proposals in five steps:

1. **Candidates.** Declared foreign keys give relationships with confidence 1.0. Name matches give relationships with confidence 0.6. A name match needs a unique key on the one side. That key is the primary key, a unique column of the same name, or a unique `id` column of the named table.
2. **Draft.** The model gets the catalog text and a JSON schema. Each name in the schema is an enum of catalog members. The model returns datasets, a time field, dimensions, relationships, and up to 12 typed metrics.
3. **Changes.** Code checks each name in the draft against the catalog again, because a provider can ignore the schema. Code makes graph changes in confirmation order: source, datasets, fields, relationships, metrics. An item that fails goes to the dropped list with its reason.
4. **Preview.** The projection builds a `SemanticModel` from the changes, over the confirmed model of the source.
5. **Probes.** A relationship runs one statement that counts the rows and the distinct keys of its one side. A metric compiles and runs once with a limit of one row. Every statement passes the read-only check and runs on the source with a row cap and a timeout. Folio probes each metric without the failed relationships. Thus a metric that needs one fails with "No join path".

Before the probes, Folio drops a dataset that is live under another source, and each field, relationship, and metric that uses it. It also drops a change whose id is live as another kind. Folio drops each item that fails its probe. If a relationship joins on columns that are not a declared key, a passed probe adds them to the unique keys of the one side. Each item that passes becomes a proposal with the reason `discovery:<run id>`. The tables `discovery_runs` and `discovery_probes` keep the run and the probe of each proposal. A rerun skips an item when the live node or a pending proposal has the same payload.

Example on the retail demo: the probe of `sales_stores` says "stores.store_id is unique over 120 rows", and the probe of `revenue` says "Revenue: $42.4M".

### Confirmation and certification

A person confirms, renames, or rejects each proposal on the model page. The graph listener writes the derived edges at confirm time. Thus a metric confirmed before its field fails, and the page shows "Confirm sales.revenue first". **Confirm all** confirms in kind order, then makes one more pass for a ratio that waits on another metric. Rename changes the label and never the id. The renamer becomes the proposer of the new proposal. The table `proposal_lineage` keeps every proposer of an item, and a rename copies the list forward. A person in that list cannot confirm or reject the item, on the model page or through the graph API. Both paths use the same functions, `confirm_proposal` and `reject_proposal`. Example: omar imports a field, and ana renames it. Then neither omar nor ana can confirm it. The model page confirms and renames only proposals from discovery or an import. Both confirm routes use one function, `confirm_proposal`. If the change is to a live node, that function carries it into the reports that use the node (`apply_confirmed`).

An expression metric with `certified` false needs one more step. An owner reads its SQL text and certifies it. The certification is a new version of the metric with the reason "SQL approved by an owner".

### Ossie mapping

`living_reports/semantic/ossie.py` maps the model to an Apache Ossie 0.2.0.dev0 document and back. Folio does not use the Ossie package; small Pydantic models keep unknown keys.

| Folio | Ossie |
|---|---|
| model name | `name` |
| dataset (`table`, `primary_key`, `unique_keys`) | dataset (`source`, `primary_key`, `unique_keys`) |
| plain field | field, `expression.sql` = the column |
| computed field | field, `expression.dialects` = `[{dialect: DUCKDB, expression: ...}]` |
| field type | `datatype` (`String`, `Integer`, `Decimal`, `Boolean`, `DateTime`, `Date`) |
| dimension, time dimension | `dimension: {is_time: false}`, `dimension: {is_time: true}` |
| synonyms | `ai_context.synonyms` |
| relationship | relationship (`from`, `to`, `from_columns`, `to_columns`); `role` in the `folio` extension |
| metric | metric with SQL text in `expression.dialects`; `description` = the definition |
| typed form, label, format, certification | `custom_extensions: [{vendor_name: folio, data: <JSON>}]` |

The SQL text of a typed metric comes from the compiler, with filter values as literals. A ratio is `<numerator> / NULLIF(<denominator>, 0)`. Example: gross margin is `SUM(sales.gross_profit) / NULLIF(SUM(sales.revenue), 0)`.

An import is one more producer of proposals, with the reason `import:<run id> (by <user>)`. The importer is the proposer, so another owner confirms the items. A dataset keeps its time field in a `folio` extension. The probes run as for discovery. A document comes from outside, so Folio checks each part:

- Names go through the payload validators.
- A computed field must be one row expression from the node allowlist.
- A metric with only SQL text goes through `parse_expression` and stays uncertified.
- Folio parses an expression metric from the `folio` extension again from its SQL text. That metric also stays uncertified.

### Editions

A freeze writes the edition row, an `Edition` node, a `built_from` edge to the report, and a `cites` edge to each metric. These writes are one transaction. If the graph write fails, no edition row stays. Each edition records `known_at` and `graph_version`. The `graph_version` is the stamp at `known_at`, taken before the edition writes its own rows. The function `reproduce(edition, store)` compiles the edition queries again from the graph as known at `known_at`, and gives the same SQL.

### Store

All application state is in one SQLite file, `<LR_DATA_DIR>/app.sqlite`, in write-ahead log mode. Each process opens one connection, and a lock serializes access to it. The graph tables and the application tables share this connection, so one transaction can write both. Analytical data is not in this file. Folio reads it only through a source (section 8).

## 8. Query path

### Sources

Each read of analytical data goes through a `Source` (`living_reports/sources/`). A source has an id, a dialect, and four operations:

- `run(sql, params, limit, timeout_s, as_of)` runs one read-only `SELECT` and returns an Arrow table. The default cap is 1,000 rows and the default timeout is 20 s. If the statement gives more rows, the table has `limit` rows and the flag `truncated`. If the statement runs too long, the source raises `SourceTimeout`, and the source stays usable.
- `introspect()` returns a `Catalog`. The catalog has the tables, the columns with their types, the keys, and the row counts. It also has column statistics: the distinct count, the null rate, and three sample values.
- `data_time()` returns the time of the data. Each `run` result also carries the data time of the data that it read, and each result and fact records that value. For a DuckDB file, it is the modification time of the file. For Postgres, it is the server time now.
- `check()` raises `SourceError` if the source cannot answer `SELECT 1`. The error text never holds a path, a DSN, or a password.

`DuckDBSource` attaches the file read-only to a new in-memory DuckDB instance. Then it disables external access and locks the configuration. Thus a query cannot write, read another file, or load an extension. A build writes a new file and replaces the old file with `os.replace`. A query that started on the old file ends on the old file. The next query attaches the new file.

`PostgresSource` opens one connection for each run, with read-only transactions and a statement timeout. It wraps the statement in `SELECT * FROM (...) AS q LIMIT n`, so the server stops after `limit + 1` rows. Its catalog has estimated row counts from `ANALYZE`, and Folio keeps the catalog for 10 minutes. The compiler renders `%s` placeholders for it. A `%` in a string literal becomes `%%`, so the driver does not read it as a placeholder. Plain Postgres has no as-of reads, so a source refuses an `as_of` argument with a sentence.

Each result and each fact records `source_id` and `data_time`. An edition records the newest data time of its queries. The result cache uses the query hash, the source id, and the data time as its key. Example: at moment 1, `order_count` on the test data is 2,800. After the demo moves to moment 2, the data time changes, the cache misses, and the query returns 3,000.

`LR_SOURCES` adds sources as JSON. A `duckdb` source names a database file (`path`) or Parquet and CSV files (`files`). Folio copies `files` into one DuckDB file once, because a view over a file needs external access. A `postgres` source names a DSN. `GET /api/health` checks each source and answers 503 when one fails.

### Compilation of a query

The package `living_reports.engine` compiles a JSON query into one SQL statement. The function `compile(query, model, dialect)` reads only the `SemanticModel`. It does not read the graph or a database.

### The query form

```json
{
  "metrics":    ["revenue"],
  "dimensions": ["sales.channel"],
  "filters":    [{"member": "stores.region", "op": "eq", "values": ["East"]}],
  "time":       {"field": "sales.sale_date", "start": "2025-01-01", "end": "2026-01-01"},
  "order":      [["revenue", "desc"]],
  "limit":      null
}
```

- A dimension or a filter names a member id, `dataset.field`. A member on a joined dataset needs no special syntax.
- The filter operators are `eq`, `ne`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `between`, `is_null`, and `contains`. `contains` finds the value as text: a `%` or `_` in the value is not a wildcard. A filter group `{"and": [...]}` or `{"or": [...]}` can nest.
- `having` filters a metric value after aggregation, for example `{"metric": "revenue", "op": "gt", "values": [1000000]}`.
- `time.end` is exclusive. If `time.field` is empty, the compiler uses the time field of the first base dataset. If that dataset has no time field, the compiler refuses the query with a sentence.
- `time.grain` (`day`, `week`, `month`, `quarter`, or `year`) adds a truncated time column. The column name is `{field}:{grain}`, for example `sales.sale_date:month`.

### Compilation

1. **Resolve members.** If the model has no entry for a metric or a member, the compiler raises `OutOfScope` with the list of unknown ids.
2. **Find the join path.** The compiler goes from the base dataset of each metric to each dataset that the query needs. It uses only many-to-one steps: the `to` columns must be the primary key or a unique key. A path has two hops at most. Each join is a `LEFT JOIN`. If a path crosses sources, the compiler refuses the query with a sentence such as "sales is in warehouse, stores is in crm; cross-source joins are not supported". If no path exists, the sentence names the metric, the member, and the dataset that has no link. The path detail goes to the `detail` field of the API answer.
3. **Lower the grain.** Metrics on one base dataset compile to one `SELECT ... GROUP BY`. Metrics on different base datasets compile to one subquery each, grouped by the same keys. The outer query joins the subqueries on the keys with a `FULL OUTER JOIN`. Thus the rows of one metric never multiply the rows of another metric.
4. **Build the AST.** The compiler uses sqlglot builders. Each filter value is a placeholder, so a value never appears in the SQL text.
5. **Render.** `ast.sql(dialect=...)` gives the SQL text. The placeholder is `?` for DuckDB and `%s` for Postgres. Folio has no dialect code.
6. **Check.** `check_select` confirms one `SELECT`. It resolves each table in its own scope: a table must be a CTE that the scope can see or a dataset of the model. It refuses table and file functions, such as `read_csv`, and functions that sqlglot does not know. It refuses a `*` projection, but it accepts `count(*)`. It also refuses `PIVOT`, a positional column such as `#1`, and a column list on a dataset table such as `orders o(a, b)`. If sqlglot cannot walk a part of the statement, the check refuses the statement. Then sqlglot `qualify` resolves each column to a declared field.

The result also has a plan: the base datasets, the joins, the group columns, the time field, and the versions of each metric and member. The query hash covers the SQL text, the parameters, and the plan, but not the slice stamp. Thus a new report node does not empty the result cache.

### Worked example

"What was the revenue by channel in the East region in 2025?" gives the query above. The compiler finds `sales_stores` and confirms that `stores.store_id` is the primary key of `stores`:

```sql
SELECT sales.channel AS "sales.channel", SUM(sales.revenue) AS "revenue"
FROM main.sales AS sales
LEFT JOIN main.stores AS stores ON sales.store_id = stores.store_id
WHERE stores.region = ? AND sales.sale_date >= ? AND sales.sale_date < ?
GROUP BY sales.channel
ORDER BY "revenue" DESC, "sales.channel" ASC
-- params: ['East', 2025-01-01, 2026-01-01]
```

On the retail demo at moment 1, the rows are Store 3,745,507.42; Online 2,384,080.85; and Click & collect 826,485.15.

A query can use metrics on two base datasets. The compiler then groups each metric on its own base dataset, each in its own subquery, and joins the subqueries on the shared dimensions. Each subquery reads its own base table, so a count of rows on a joined dataset comes from the rows of that dataset, not from the rows of the fact table.

### Expression metrics

An expression metric is one aggregate over one dataset, for example `sum(sales.revenue) filter (where sales.channel <> 'Online')`. The text names each field by its member id, not by its column. `parse_expression` accepts only listed node classes: listed aggregates, arithmetic, comparisons, boolean logic, `CASE` and `IF`, casts to listed types, `COALESCE` and `NULLIF`, date parts, literals, and columns. It refuses all other nodes, for example `read_csv` or a window. It also refuses a second aggregate, a subquery, a column that is not a field of the dataset, and a dialect other than `duckdb` or `postgres`. The payload stores the sqlglot AST and the list of fields, so `dependents(field)` finds the metric. The compiler renders the AST in the target dialect.

### The order for each question

1. Use the query engine for certified metrics.
2. If the engine cannot answer, return its sentence to the harness.
3. Offer read-only exploratory SQL, with an Unverified label. The exploratory SQL can read only the tables and the columns of the model. The check gives a qualified AST, and Folio runs the SQL that this AST renders, not the text from the model. The source must also read that SQL as one `SELECT`.

Exploratory answers cannot enter a team or business-critical report until a person promotes the logic to a certified metric.

### Changes to a section

The module `harness/commands.py` holds each operation on one section. Each operation saves through `save_report`, the one place that sets the cost, builds the cube, saves the report, and links it in the graph. The routes are in `api/routes_sections.py`, under `/api/reports/{id}/sections/{sid}/`.

- `rerun_section` runs an edited query for one section. It keeps each paragraph whose tokens still resolve and removes the others. It makes no model call.
- `with_selection` adds what the reader selected to the routed query, in this order: the filters of the selected section, the filters of the question, the member keys of the selected number, and the selected filters. A later filter on the same member replaces an earlier one. A selected month range sets the dates.
- `swap_chart`, `remove_section`, `edit_prose`, `reorder_blocks`, and `remove_block` make no model call.
- `rewrite_section` (Shorter, Expand, Regenerate wording) makes one structured call for each attempt, with two attempts. The words pass `tokenize_values` and `validate_spec`. If both attempts fail, nothing changes.

Section ids are stable. Example: if a person removes `s1` from a report with `s1` and `s2`, the next question makes `s3` and its query `q3`. A removal closes the threads on the section, and a chip change closes the threads on numbers that no longer exist.

An edit of words needs editing rights: the author role or the author of the report. Rights never come from the contributor list. A change of values or words adds the user to the contributors: a chip change, a question, Shorter, Expand, Regenerate wording, Edit wording, and Remove section. A chart swap and a block move or removal do not. A contributor cannot sign, so the page warns an owner or a compliance user once before such a change. Each change returns the draft to the changed state. The checker runs on the next edition. The report cost is the sum of all turns.

Each report also carries a cube. The cube holds each metric as additive components at the grain of the report dimensions and month, up to 5,000 cells. The cube reads only the rows and dates of the widest report query, plus one previous period of the same length. Every filter value is a parameter. For a top query, the cube keeps only the members that the query shows. Thus an exported file cannot show rows that the report does not cover. The module `reports/explore.py` compiles the cube as one engine query. The metric type sets the components: a sum or a count carries itself, an average carries a sum and a count, and a ratio of two sums or counts carries both metrics. A count distinct, a minimum, a maximum, or an expression is carried but is not re-aggregated. The module also builds the scoped engine query for drill and pin. The cube runs on the report's source. Its identity holds the query hash, the source id, and the data time of the source. Thus new data gives a new cube when the report refreshes. Drill and pin also run on the report's source.

## 9. Checker

Compliance owns the rules. Business owners own metric definitions and attestation. The author of a report cannot approve that report.

The checks run in this order:

1. **Trace numbers.** Each number must link to a stored query result. The renderer marks only traced numbers as verified.
2. **Compare totals.** The checker reconciles control totals, row counts, and date coverage.
3. **Apply rules.** The checker applies disclosure rules, banned claims, and certified metric versions.
4. **Challenge wording.** A different model reviews causal claims. This step is advisory, and it cannot pass a report alone.
5. **Get sign-off.** The required people review and sign.

The checker uses separate credentials and a rule set that authors cannot change. Test the checker with reports that contain planted errors, and record the share of errors that it finds.

The checker gives evidence. It does not give regulatory compliance by itself. Get legal review before making a compliance claim.

## 10. Model harness

The harness uses the cheapest step that can give a correct answer.

1. Reuse a cached result or an existing definition, with no LLM call.
2. Route the question with a decision model when it is a plain choice of metric, breakdown, and grain. Code adds the values, the period, and the count that the question names. Code sets no grain when the question has no time word. The question must name its metric, because a choice over the metrics always selects one. Each id comes from the semantic model, so the route cannot name a field that does not exist.
3. Fill a validated specification with a small model when the decision is not confident, or when the question needs an exclusion, a threshold, two metrics, or rows. If the question names no metric, the router's metric is a guess. Folio then asks the person to select the router's metric or the most probable other metric of the decision model.
4. Use code mode for multi-step work.
5. Escalate to a frontier model only when validation fails or the decision model doubts the route.

The main measure is cost per correct answer. Build a test set of questions with expected values from DuckDB. Run the test set for each model tier. Small-model support is a hypothesis until these tests pass.

`living_reports/harness/providers.py` supports Anthropic, OpenAI, Google, and Ollama. `call_json` in `living_reports/harness/llm.py` adds the JSON schema, the validation and repair, and the token counts.

## 11. Code mode

Define tools with MCP schemas. Generate TypeScript declarations from these schemas. The model writes TypeScript that calls these typed functions.

WARNING: Generated code is untrusted. A sandbox escape can expose data and credentials. Keep all credentials and data connections in the host process.

Run generated code with these limits:

- Start a new Deno process for each run.
- Grant no Deno permissions. Use no `--allow-run` and no `--allow-ffi`.
- Send tool calls over stdin and stdout to a gateway in the host process.
- Set limits for memory, CPU time, and wall time at the operating system level.
- Add a container or a microVM before any shared deployment.

The sandbox only coordinates work. DuckDB does the data computation, so the design keeps the rule that all compute is vectorized. Row data stays out of the model context.

Deno is not installed on the development host. Install it as part of the first phase.

## 12. Repository structure

| Path | Purpose |
|---|---|
| `folio_graph/` | The graph engine. It does not import `living_reports`. |
| `living_reports/demos/` | The demo datasets: one module for each demo (for example `retail.py` and `hr.py`) with its generator, model seed, moments, definition change, and eval questions. `orders.py` wraps the unit-test model. |
| `living_reports/semantic/` | Folio kinds, derived edges, the `SemanticModel` and `Vocabulary` projections, the demo model, and the Ossie export and import (`ossie.py`) |
| `living_reports/store/` | The SQLite store and `get_store()`, one instance for each process |
| `living_reports/engine/` | The query engine: the query form, join paths, compilation, expression metrics, and the read-only check |
| `living_reports/sources/` | The `Source` protocol, the catalog types, `DuckDBSource`, `PostgresSource`, the demo source build, and the registry of configured sources |
| `living_reports/discovery/` | Discovery: relationship candidates, the draft schema and the model call, changes, the preview model, probes, runs, and confirmation |
| `living_reports/metrics/` | The query runner, facts, and exploratory SQL |
| `living_reports/reports/` | Reports, editions, sign-off, and change handling. `certified.py` holds the certified question set, `digest.py` the weekly digest, and `search.py` the report search. |
| `living_reports/harness/` | Model calls, routing, and report assembly. `commands.py` holds the operations on one section, and `selection.py` applies a selection to a query. |
| `living_reports/api/` | The FastAPI application. `routes_home.py` serves the config, the certified questions, the search, and the digest. `routes_sections.py` serves the section commands, and `routes_vocabulary.py` serves the vocabulary. `routes_model.py` serves discovery, the proposals, confirmation, certification, and the Ossie export and import. |
| `living_reports/web/` | The React and Vite application. `src/model/` is the model page. |
| `scripts/` | `run.sh` starts Folio. `load_demo_postgres.py` loads the demo data into Postgres. |

## 13. Delivery order

1. **Report core.** Build the metric compiler, the specification, the renderer, the Narrative view, and the Dashboard view with fixed test specifications.
2. **Conversation.** Add the harness, the objective routing, clarifying questions, and anchored threads.
3. **Graph and editions.** Add the context graph, editions, the checker, and the control rail.
4. **Change handling.** Add demo moment 2, the change briefing, and edition differences.
5. **Cost proof.** Add code mode, the test set, and the cost display.
6. **Optional.** Add conversational connection of a new data file.

Each phase ends with a working screen.

## 14. Risks

| Risk | Control |
|---|---|
| Small models fail on code generation | Simple questions skip code mode |
| Prompt injection through column names or values | Treat data as untrusted, and run all output through the validator |
| Numbers look verified but are wrong | Verify in the renderer, and test with planted errors |
| The graph becomes stale | Seed it from data profiles, and keep the scope small |
| Token savings do not match published figures | Measure on our own test set, and report only those results |

## 15. Decisions

**Design decisions:**

- A bi-temporal context graph with no heavy memory layer.
- A persona-agnostic design, led by the objective of the question.
- Small and frontier model support, MCP code mode, and Deno.
- A living report with separate HTML pages.
- An MVP on static synthetic data, with one vertical.
- Compliance and business leaders own the automated checker.
- The Editorial and Warm studio blend.
- The demo shows the business-critical sign-off.

**Decided by the designer, open to change:**

- Telco as the demo vertical, with card as the finale.
- The four-stage model, the three tiers, and the four views.
- The system selects the view from the objective.
- The change rule of one human approval for a change in meaning.
- A DuckDB graph, and a compiled metric path.
- Stdio transport with zero Deno permissions.
- The name and paths in this document.

**Left to the builder:**

- The exact specification schema.
- Font files, within the visual direction. Exported files must embed their fonts.
- The chart library, within the single renderer.
- Dark mode colors, validated against their own surfaces.

## 16. Research basis

The evidence varies in quality. Much of the material comes from vendors. The BCBS 239 and PCAOB points come from secondary sources, because automated access to the primary pages failed.

- [dbt semantic layer benchmark](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026)
- [Anthropic: code execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp)
- [Cloudflare: Code Mode](https://blog.cloudflare.com/code-mode/)
- [Deno security guidance](https://docs.deno.com/runtime/fundamentals/security.md)
- [Proof-Carrying Numbers](https://arxiv.org/abs/2509.06902)
- [Zep temporal knowledge graph paper](https://arxiv.org/abs/2501.13956v1)
- [Databricks trusted assets](https://docs.databricks.com/genie/trusted-assets)
- [Snowflake verified queries](https://docs.snowflake.com/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository)
