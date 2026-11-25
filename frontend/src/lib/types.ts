export type Verdict = "match" | "conflict" | "unsupported";

export interface AnswerValue {
  metric: string;
  value: string;
  unit: string;
}

export interface Citation {
  proposition_id: string;
  source_uri: string;
  filing_type: string;
  filing_date: string;
  page_index: number;
  page_number: number;
  evidence: string;
}

export interface Claim {
  claim_id: string;
  claim_text: string;
  is_numeric: boolean;
  verdict: Verdict;
}

export interface Verification {
  claims: Claim[];
  verified_count: number;
  total_count: number;
  score: number;
}

export interface PipelineInfo {
  name: string;
  sub_query_count: number;
  fact_count: number;
  latency_ms: number;
  models: Record<string, string>;
}

export interface Step {
  step_id: number;
  question: string;
  answer_text: string;
  answerable: boolean;
}

// Step 4.7: the road taken and why, in plain words. `several_versions_note`
// is null unless the "several near-identical results" check actually found
// two genuinely different values for the same period (an original figure
// and a restated one).
export interface HowItHappened {
  road: string;
  reason: string;
  question_understood: string;
  pieces: string[];
  retry_attempted: boolean;
  retry_names_tried: string[];
  several_versions_note: string | null;
  steps: Step[];
  checked_against_original_question: boolean;
  summary: string;
}

export interface AskResponse {
  question: string;
  status: "answered" | "abstained";
  answer_text: string;
  abstention_message: string;
  answers: AnswerValue[];
  citations: Citation[];
  verification: Verification;
  confidence_score: number;
  pipeline: PipelineInfo;
  how_it_happened: HowItHappened;
}

export interface SystemInfo {
  pipeline: string;
  companies: string[];
  documents: string[];
  proposition_count: number;
  models: Record<string, string>;
  document_types: string[];
}

export interface DocumentUploadResponse {
  doc_id: string;
  status: "accepted" | "skipped_already_there";
}

export interface DocumentEvent {
  seq: number;
  type: string;
  message: string;
  data: Record<string, unknown>;
}

export interface DocumentEventsResponse {
  events: DocumentEvent[];
  done: boolean;
  failed: boolean;
}

// ---- Experiment Results tab (static, from public/results/full366.json) ----

export type SetupName = "plain_ai" | "c0" | "c4_plus";
export type CollisionAxis = "entity" | "metric" | "temporal";
export type Outcome = "correct" | "incorrect" | "abstained";

export interface CIStat {
  value: number;
  lo: number;
  hi: number;
}

export interface AxisMetricsCI {
  n: number;
  correct: number;
  incorrect: number;
  abstained: number;
  precision: CIStat | null;
  recall: CIStat | null;
  coverage: CIStat | null;
  confidently_wrong: CIStat | null;
}

export interface RagasMetric {
  valid: number;
  total: number;
  mean: number | null;
}

export interface RagasSummary {
  faithfulness: RagasMetric;
  answer_relevancy: RagasMetric;
}

export interface ResultCitation {
  proposition_id: string;
  source_uri: string;
  filing_type: string;
  filing_date: string;
  page: number | null;
  evidence: string;
}

export interface ResultVerificationClaim {
  claim_id: string;
  claim_text: string;
  verdict: Verdict;
  is_numeric: boolean;
}

export interface SetupPipelineInfo {
  sub_query_count?: number;
  fff_count?: number;
  retrieved_chunk_count?: number;
  models: Record<string, string>;
}

export interface SetupAnswer {
  outcome: Outcome | null;
  answer_text: string;
  answers: AnswerValue[];
  citations: ResultCitation[];
  cost_usd: number;
  pipeline: SetupPipelineInfo;
  verification_trace?: ResultVerificationClaim[];
  confidence_score?: number | null;
  ragas?: { faithfulness: number | null; answer_relevancy: number | null };
}

export interface ResultQuestion {
  collision_id: string;
  question: string;
  axis: CollisionAxis;
  scale: string | null;
  gold_answers: Record<string, number> | null;
  setups: Partial<Record<SetupName, SetupAnswer>>;
}

export interface ResultsData {
  generated_at: string;
  n_questions: number;
  overall: Record<SetupName, AxisMetricsCI>;
  by_axis: Record<CollisionAxis, Partial<Record<SetupName, AxisMetricsCI>>>;
  ragas: RagasSummary | null;
  cost: Record<SetupName | "ragas", number>;
  questions: ResultQuestion[];
}
