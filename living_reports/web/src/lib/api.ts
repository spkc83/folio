export type FactT = {
  id: string;
  label: string;
  value: number | string | null;
  formatted: string;
  query_hash: string;
  metric: string | null;
  metric_version: number | null;
  source_id: string;
  /** The source's data time when the value was read (ISO text). Null on old records. */
  data_time?: string | null;
  state: "ok" | "empty" | "undefined";
  definition?: string | null;
  binding?: Binding | null;
  /** How code derived the value, for a derived fact ("Sum of 6 monthly values"). */
  basis?: string | null;
};

/** Where a fact sits in its query: the ref, the metric, and the member keys. */
export type Binding = { ref: string; metric: string; keys: Record<string, string | null>; share: boolean };
/** One filter of a stored query: a member id, an operator, and its values. */
export type QueryFilter = { member: string; op: string; values: (string | number | boolean)[] };
export type QueryFilterGroup = { and?: (QueryFilter | QueryFilterGroup)[]; or?: (QueryFilter | QueryFilterGroup)[] };
export type QueryTime = { field?: string | null; start?: string | null; end?: string | null; grain?: string | null };
/** A stored report query in the query engine's form. Dimensions are member ids ("orders.sales_channel"). */
export type QueryDef = {
  metrics: string[]; dimensions?: string[]; filters?: (QueryFilter | QueryFilterGroup)[];
  time?: QueryTime | null; limit?: number | null; having?: unknown[]; order?: [string, string][];
};
/** A metric or a dimension that people and the model can name (`GET /api/vocabulary`). */
export type Term = { id: string; label: string; definition?: string; synonyms?: string[]; format?: string | null; is_time?: boolean; dataset?: string | null };
/** One modeled column of a dataset, with its profile (`GET /api/sources/{id}/schema`). */
export type SchemaColumn = {
  field_id: string; name: string; label: string; type: string; dimension: boolean; is_time: boolean; synonyms: string[];
  distinct_count: number | null; null_rate: number | null; samples: string[]; min: string | null; max: string | null;
};
export type SchemaDataset = { name: string; table: string; row_count: number | null; primary_key: string[]; columns: SchemaColumn[] };
/** What a person can ask about on one source, and its modeled columns. */
export type SourceSchema = { source_id: string; profiled: boolean; metrics: Term[]; dimensions: Term[]; datasets: SchemaDataset[] };
export type Vocabulary = { metrics: Term[]; dimensions: Term[]; time_fields: string[]; grains: string[]; model_version: number };
export type MetricAgg = { kind: "sum" | "count" | "ratio" | "other"; num: string; den?: string | null; format: string; label: string };
/** Metric components at the grain of the report's dimensions plus month. */
export type CubeView = {
  dimensions: string[]; labels: Record<string, string>; vocabulary: Record<string, string>;
  metrics: Record<string, MetricAgg>; columns: string[]; rows: unknown[][];
  span: [string | null, string | null]; query_hash: string;
  /** The source and its data time: a new data time gives a new cube. */
  source_id?: string | null; data_time?: string | null;
};

export type Block =
  | { type: "prose"; text: string }
  | { type: "tile"; fact_id: string; label: string; delta?: "previous_period" | null; delta_label?: string | null; spark_ref?: string | null }
  | { type: "bars" | "line" | "table" | "small_multiples" | "segmented"; query_ref: string; title: string }
  | { type: "delta_bars"; query_ref: string; against_ref: string; title: string }
  | { type: "action"; text: string; basis?: string[] };

/** How a section's own words were checked. Code proves the claims; this check is weaker. */
export type Review = {
  wording: "connectors" | "model" | "code" | "none";
  omissions: "model" | "none";
  /** The model that reviewed the words: for auditors only. */
  model?: string | null;
  /** A refresh changed a value after the review. */
  outdated?: boolean;
};

export type Section = {
  id: string; heading: string; intent: string; blocks: Block[]; decisions: string[];
  /** "code": the model's draft did not pass the checks, so code wrote the words. */
  narration?: "model" | "code";
  /** Claims that no longer hold on the new data. */
  stale_claims?: string[];
  /** Follow-up chips, most useful first: code makes them, a decision model orders them. */
  follow_ups?: { label: string; query: QueryDef }[];
  /** How the words between the claims were checked. Missing on a section written before it was recorded. */
  review?: Review | null;
};
export type Assumption = { id: string; text: string };
export type Spec = {
  title: string; kicker: string; objective: string; assumptions: Assumption[]; sections: Section[];
  /** The checked claims of the report, by id. Text cites them with `{claim:id}` tokens. */
  claims?: Record<string, Claim>;
};

/** One row of a claim's comparison table. `fact` is a fact id. */
export type EvidenceRow = { label: string; fact: string; highlight: boolean };
/** One period of a claim's series. `fact` is a fact id. */
export type EvidencePoint = { label: string; fact: string; partial?: boolean };
/** What the evidence drawer shows for one claim. */
export type Evidence = {
  counted: string;
  by?: string | null;
  rows: EvidenceRow[];
  series?: EvidencePoint[] | null;
  definition?: string | null;
};
export type ClaimKind = "value" | "compare" | "rank" | "share" | "extreme" | "change" | "trend" | "target" | "peak";
/** A statement that code computed and checked. `text` can hold `{fact:id}` tokens. */
export type Claim = {
  id: string;
  kind: ClaimKind;
  text: string;
  facts: string[];
  query_ref: string;
  segment?: Record<string, string>;
  /** Inclusive first and last day. */
  period?: [string, string] | null;
  evidence: Evidence;
  /** The values the claim template was filled with. */
  params?: Record<string, unknown>;
  /** The claim's finding word ("highest", "below", "rose sharply") for a `{word:id}` token. */
  word?: string | null;
  /** The member labels the claim is about. */
  about?: string[];
};
export type Point = { label: string; value: number | string | null; formatted: string; fact_id: string };
export type QueryView = {
  columns: string[];
  rows: unknown[][];
  query_hash: string;
  points: Point[];
  series?: string | null;
  /** Column id (member id, time column, or metric id) to the label people read. */
  labels?: Record<string, string>;
};

/** The label of a result column, or its id when the query has no label for it. */
export const columnLabel = (query: QueryView, column: string): string => query.labels?.[column] ?? column;
export type View = "narrative" | "dashboard" | "brief" | "evidence";

export type Payload = {
  id: string;
  spec: Spec;
  facts: Record<string, FactT>;
  queries: Record<string, QueryView>;
  edition: number | null;
  state: "draft" | "published";
  tier: "exploratory" | "team" | "business_critical";
  view: View;
  cost?: Cost;
  cube?: CubeView | null;
  cube_reason?: string | null;
  query_defs?: Record<string, QueryDef>;
  verified?: Verified | null;
  /** Who wrote the report and who changed it. Only these people and authors can change its words. */
  author?: string;
  contributors?: string[];
};

/** What the reader pointed at. The server narrows the routed query with it, in code. */
export type Selection = { filters: QueryFilter[]; range: [string, string] | null; section_id?: string | null; fact_id?: string | null };

/** The answer of a section command (`/api/reports/{id}/sections/{sid}/…`). */
export type CommandResult = { kind: "ok" | "failed"; message?: string | null; wording_dropped?: boolean; wording_stale?: boolean; cost: Cost };

export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`Request failed with status ${res.status}`);
  return res.json() as Promise<T>;
}

export type ThreadMessage = { role: "user" | "assistant"; text: string };
export type Thread = {
  id: string;
  report_id: string;
  section_id: string;
  fact_id: string | null;
  messages: ThreadMessage[];
  status: "open" | "closed";
  decision: string | null;
};
export type Clarify = { question: string; options: { label: string; query: QueryDef }[] };
export type Cost = { input_tokens?: number; output_tokens?: number; usd?: number | null; calls?: number; models?: string[]; rung?: string };
export type AskResult = {
  kind: "report" | "clarify" | "out_of_scope" | "failed";
  report_id?: string | null;
  view?: View | null;
  edition?: number | null;
  clarify?: Clarify | null;
  message?: string | null;
  /** The technical cause of a refusal, for example the join path search. */
  detail?: string | null;
  can_explore?: boolean;
  cost: Cost;
};
export type ReportSummary = { id: string; title: string; tier: string; author: string };

export async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-LR-User": currentUser() },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new ApiError(detailText(error.detail) ?? `Request failed with status ${res.status}`, res.status, error.current_edition ?? null);
  }
  return res.json() as Promise<T>;
}

/** A GET that names the demo user, for answers that differ per user. */
export async function getAsUser<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { "X-LR-User": currentUser() } });
  if (!res.ok) throw new Error(`Request failed with status ${res.status}`);
  return res.json() as Promise<T>;
}

export async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-LR-User": currentUser() },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new ApiError(detailText(error.detail) ?? `Request failed with status ${res.status}`, res.status, error.current_edition ?? null);
  }
  return res.json() as Promise<T>;
}

export function requestFailed(error: unknown): AskResult {
  return { kind: "failed", message: error instanceof Error ? error.message : "The request failed.", cost: {} };
}

export const reportUrl = (id: string, view?: string | null) => `/r/${id}${view ? `?view=${view}` : ""}`;
/** A certified answer opens its published edition; other answers open the live report. */
export const answerUrl = (r: AskResult) =>
  r.edition ? `/r/${r.report_id}/e/${r.edition}${r.view ? `?view=${r.view}` : ""}` : reportUrl(r.report_id ?? "", r.view);

export type CheckResult = { step: "trace" | "totals" | "rules" | "wording"; status: "pass" | "fail" | "open"; findings: { where: string; detail: string }[] };
export type Signature = { role: string; signer: string; content_hash: string; signed_at: string };
export type EditionState = {
  number: number; content_hash: string; tier: Payload["tier"]; status: string; author: string;
  checks: CheckResult[]; signatures: Signature[]; needs: string[]; can_publish: boolean; newest: number;
};
export type Publication = { edition: EditionState | null; draft_changed: boolean };

/** The text of an error `detail`: a sentence, or the first message of a validation list. */
function detailText(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0 && typeof detail[0]?.msg === "string") return detail[0].msg;
  return null;
}

export class ApiError extends Error {
  constructor(message: string, public status: number, public currentEdition: number | null) { super(message); }
}

/** The seeded demo users and their roles. The server is the authority; the page uses this only to grey out controls. */
export const DEMO_ROLES: Record<string, string> = { ana: "author", omar: "owner", priya: "owner", chidi: "compliance" };

/** Same rule as the server's `can_edit`: the author role or the report's author. Never a contributor. */
export const canEditWords = (user: string, author?: string): boolean => DEMO_ROLES[user] === "author" || user === author;

/** A role that signs editions. A content change makes that user a contributor, who cannot sign. */
export const isSigner = (user: string): boolean => ["owner", "compliance"].includes(DEMO_ROLES[user] ?? "");

const USER_KEY = "lr-demo-user";
export function currentUser(): string {
  try { return localStorage.getItem(USER_KEY) ?? "ana"; } catch { return "ana"; }
}
export function setCurrentUser(user: string): void {
  try { localStorage.setItem(USER_KEY, user); } catch { /* The demo user then stays the default. */ }
}

export type ChangeItem = {
  id: string; kind: "new_data" | "new_column" | "new_value" | "definition" | "question"; title: string; detail: string;
  affected_reports: string[]; status: string; proposal_id: string | null; attention?: string[];
  is_new?: boolean; detected_at?: string | null; subject?: Record<string, string>;
};
export type Impact = { reports: string[]; before: { label: string; formatted: string }; after: { label: string; formatted: string } };
export type FactChange = { id: string; label: string; before: string; after: string };
export type EditionDiff = {
  changed_facts: FactChange[]; added_facts: FactChange[]; removed_facts: FactChange[];
  added_sections: string[]; removed_sections: string[];
  definition_changes: { metric: string; from_version: number; to_version: number; definition: string | null }[];
};

// Home (milestone 9).
/** The deployment's flags, the demo's example question and starter questions, and the
 * demo datasets a person can select (none when the deployment has one fixed dataset). */
export type Config = {
  demo: boolean; placeholder?: string; starters?: string[];
  dataset?: string; datasets?: { name: string; title: string }[];
};
export type Verified = { by: string; at: string; current: boolean };
export type CertifiedQuestion = { id: string; question: string; report_id: string; verified_by: string; verified_at: string; current: boolean };
export type SearchHit = { report_id: string; title: string; tier: string; labels: string[] };
export type SuggestedQuestion = { question: string; certified_id?: string | null; query?: unknown };
export type Digest = { since: string; until: string; top_changes: ChangeItem[]; suggested_questions: SuggestedQuestion[] };

// The model page (milestone 8): discovery and import proposals.
export type Probe = { target: string; kind: "uniqueness" | "metric"; status: "pass" | "fail"; detail: string; value: string | null; sql: string };
export type ProposalView = {
  proposal: { id: string; status: string; proposed_by: string; reason: string; change: { id: string; kind: string; payload: Record<string, unknown> } };
  probe: Probe | null; run_id: string | null; kind: string; node_id: string; summary: string;
};
export type Dropped = { name: string; kind: string; reason: string };
export type DiscoveryRun = { id: string; source_id: string; proposal_ids: string[]; dropped: Dropped[]; skipped: string[] };
export type ConfirmResult = { confirmed: string[]; failed: [string, string][] };
export type ImportResult = { run: DiscoveryRun; dropped: Dropped[] };

/** The steps of a running answer (`/api/progress/{id}`). A step with no `seconds` is running. */
export type ProgressStep = { key: string; label: string; seconds: number | null };
export type ProgressView = { steps: ProgressStep[]; seconds: number; done: boolean };

/** A new id for the progress of one question. The page sends it with the question. */
export const newProgressId = () => `p-${crypto.randomUUID()}`;
