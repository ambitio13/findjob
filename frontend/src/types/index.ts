// Centralized API types mirroring backend schemas.

export interface HealthResponse {
  status: string;
  app: string;
  env: string;
  version: string;
  db: string | null;
  redis: string | null;
}

export interface PaginatedMeta {
  page: number;
  page_size: number;
  total: number;
}

export interface JobOut {
  id: string;
  platform: string;
  company: string;
  title: string;
  location: string | null;
  salary_range: string | null;
  direction: string | null;
  jd_raw: string;
  /** Structured parse draft stored in the ``jd_normalized`` JSON column. */
  jd_normalized: Record<string, unknown> | null;
  created_at: string | null;
}

export interface JobListOut {
  meta: PaginatedMeta;
  items: JobOut[];
}

export interface JobCreate {
  company: string;
  title: string;
  location?: string | null;
  salary_range?: string | null;
  direction?: string | null;
  jd_raw: string;
  platform?: string;
  /** Structured parse draft persisted alongside the manual fields. */
  jd_normalized?: Record<string, unknown> | null;
}

/**
 * Partial update payload for `PATCH /jobs/{job_id}`. All fields optional;
 * only supplied (non-null) fields are applied. Used to correct a parsed draft
 * or replace the `(解析中…)` placeholder after an async parse completes.
 */
export interface JobUpdate {
  company?: string;
  title?: string;
  location?: string | null;
  salary_range?: string | null;
  direction?: string | null;
  platform?: string;
  jd_raw?: string;
}

// --- JD paste parsing (create-job-first async flow) ---

/** A field the model could not confidently extract (mirrors backend UncertainField). */
export interface JdParseUncertainField {
  field: string;
  reason: string | null;
}

/**
 * Validated structured draft fields produced by the JD paste parse workflow.
 * Mirrors `JdPasteFactsModelOutput` in backend/app/schemas/jd_paste_facts.py.
 * All fields are optional/default-empty to tolerate sparse JDs and to serve as
 * the stable empty shape on parse failure.
 */
export interface JdParseDraftFields {
  title: string | null;
  company: string | null;
  platform: string | null;
  location: string | null;
  salary_range: string | null;
  direction: string | null;
  responsibilities: string[];
  hard_requirements: string[];
  nice_to_have_requirements: string[];
  benefits_or_risk_clues: string[];
  uncertain_fields: JdParseUncertainField[];
}

/** Lightweight run summary carried in the parse response. */
export interface JdParseRunSummary {
  id: string;
  status: string;
  error: string | null;
}

/**
 * Immediate response for POST /api/v1/jobs/parse (enqueue-and-poll).
 * The endpoint creates a ``queued`` AgentRun, enqueues the parse job, and
 * returns immediately with HTTP 202. The frontend polls the run detail until
 * terminal status, then hydrates ``fields`` from ``AgentRun.result.fields``.
 */
export interface JdParseSubmitResponse {
  run: JdParseRunSummary;
  /** The job row created up front (create-job-first). Company/title start as
   * `(解析中…)` placeholders until the worker writes back the parsed draft. */
  job: JobOut;
  raw_jd: string;
  platform: string | null;
}

/**
 * Terminal run statuses — once reached, JD parse polling can stop.
 *
 * @deprecated import from `@/features/agent-runs/status` instead. Kept here
 * for backward compatibility with existing call sites that import from
 * `@/types`; new code should use `TERMINAL_AGENT_RUN_STATUSES`.
 */
export const TERMINAL_JD_PARSE_STATUSES: ReadonlySet<string> = new Set([
  "succeeded",
  "failed",
]);

/** Response shape for POST /api/v1/jobs/parse (legacy synchronous flow). */
export interface JdParseResponse {
  status: "succeeded" | "failed";
  run: JdParseRunSummary;
  fields: JdParseDraftFields;
  /** Parse provenance persisted into ``jd_normalized._extraction`` on save. */
  extraction: JdNormalizedExtraction;
  raw_jd: string;
}

/**
 * Parse provenance persisted in ``jd_normalized._extraction`` (design.md
 * §"jd_normalized shape"). Captured at parse time so the saved job carries an
 * auditable link back to the AgentRun and model that produced the draft.
 */
export interface JdNormalizedExtraction {
  status: string;
  run_id?: string;
  parsed_at?: string;
  prompt_version?: string;
  provider?: string;
  model?: string;
}

/**
 * The durable ``jd_normalized`` shape stored on a JobPosting (design.md
 * §"jd_normalized shape"). ``fields`` is the model-parsed draft snapshot;
 * ``_extraction`` holds parse provenance. The job's own columns hold the
 * user-edited final values.
 */
export interface JdNormalized {
  _extraction?: JdNormalizedExtraction;
  fields: JdParseDraftFields;
}

export interface AgentRunOut {
  id: string;
  workflow_type: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
  created_at: string | null;
}

/** Sanitized single step within an agent run (mirrors AgentStepOut). */
export interface AgentStepOut {
  id: string;
  run_id: string;
  step_no: number;
  name: string;
  status: string;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string | null;
}

/** Run detail carrying the ordered step trail (mirrors AgentRunDetailOut). */
export interface AgentRunDetailOut {
  id: string;
  workflow_type: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  result: Record<string, unknown> | null;
  created_at: string | null;
  steps: AgentStepOut[];
}

// --- Current user profile & job-search preferences ---

/**
 * Named constraint fields stored inside the `constraints` JSON column. Each
 * field is a plain string (or null when unset). The backend defines these
 * 8 keys as the known v1 set; any other keys in `constraints` are treated
 * as legacy and exposed read-only via `legacy_constraints`.
 */
export interface ProfileConstraints {
  deal_breakers: string | null;
  preferred_company_types: string | null;
  preferred_industries: string | null;
  work_mode_preference: string | null;
  commute_preference: string | null;
  career_goals: string | null;
  resume_tailoring_notes: string | null;
  availability_notes: string | null;
}

export interface UserProfile {
  id: string;
  display_name: string;
  email: string | null;
  career_direction: string | null;
  base_location: string | null;
  preferred_locations: string[] | null;
  salary_min: number | null;
  salary_max: number | null;
  strengths: string[] | null;
  /** Typed v1 constraint fields. */
  named_constraints: ProfileConstraints;
  /** Unknown constraint keys preserved from legacy data (read-only). */
  legacy_constraints: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}

/**
 * Partial update payload. All fields optional; omitting a field leaves it
 * untouched, sending `null` clears the underlying nullable column.
 *
 * `named_constraints` is itself a partial structure — only non-undefined
 * keys within it are applied by the backend.
 */
export interface UserProfileUpdate {
  display_name?: string;
  email?: string | null;
  career_direction?: string | null;
  base_location?: string | null;
  preferred_locations?: string[] | null;
  salary_min?: number | null;
  salary_max?: number | null;
  strengths?: string[] | null;
  named_constraints?: Partial<ProfileConstraints>;
}

// --- Resumes ---

/** Contact info extracted from the resume (mirrors backend Contact). */
export interface ResumeContact {
  name: string | null;
  email: string | null;
  phone: string | null;
}

/** One education entry (mirrors backend EducationItem). */
export interface ResumeEducationItem {
  school: string | null;
  degree: string | null;
  major: string | null;
  period: string | null;
}

/** One work experience entry (mirrors backend WorkExperienceItem). */
export interface ResumeWorkExperienceItem {
  company: string | null;
  title: string | null;
  period: string | null;
  summary: string | null;
}

/** One project entry (mirrors backend ProjectItem). */
export interface ResumeProjectItem {
  name: string | null;
  role: string | null;
  summary: string | null;
}

/** A field the model could not confidently extract (mirrors UncertainField). */
export interface ResumeUncertainField {
  field: string;
  reason: string | null;
}

/**
 * Validated structured facts extracted by the model. Mirrors
 * `ResumeFactsModelOutput` in backend/app/schemas/resume_facts.py. All fields
 * are optional to tolerate sparse resumes.
 */
export interface ResumeFacts {
  contact: ResumeContact | null;
  education: ResumeEducationItem[];
  work_experience: ResumeWorkExperienceItem[];
  projects: ResumeProjectItem[];
  skills: string[];
  years_of_experience: number | null;
  target_direction: string | null;
  locations: string[];
  strengths: string[];
  highlights: string[];
  uncertain_fields: ResumeUncertainField[];
}

/** Extraction status recorded in `parsed_facts._extraction`. */
export type ResumeExtractionStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "needs_confirmation"
  | "not_run";

/** Terminal extraction statuses — once reached, polling can stop. */
export const TERMINAL_EXTRACTION_STATUSES: ReadonlySet<ResumeExtractionStatus> =
  new Set(["succeeded", "failed", "needs_confirmation", "not_run"]);

/** The `_extraction` telemetry block inside `parsed_facts`. */
export interface ResumeExtractionInfo {
  status: ResumeExtractionStatus;
  extracted_at: string;
  run_id?: string;
  prompt_version?: string;
  provider?: string;
  model?: string;
}

/**
 * The full `parsed_facts` shape stored on a ResumeVersion. Parser telemetry
 * (`_parser`, `_parser_status`) is always present after upload; `facts` and
 * `_extraction` appear after the extraction workflow runs.
 */
export interface ResumeParsedFacts {
  _parser?: string;
  _parser_status?: string;
  _extraction?: ResumeExtractionInfo;
  facts?: ResumeFacts;
  [key: string]: unknown;
}

/** Full resume version, including extracted ``raw_text``. Detail view only. */
export interface ResumeVersionOut {
  id: string;
  version_no: number;
  parsed_facts: ResumeParsedFacts | null;
  raw_text: string | null;
  created_at: string | null;
}

/** Version summary without ``raw_text`` (keeps list payloads small). */
export interface ResumeVersionListItem {
  id: string;
  version_no: number;
  created_at: string | null;
  parser_status: string | null;
  parser_name: string | null;
}

/** Resume summary used in list responses. */
export interface ResumeOut {
  id: string;
  filename: string;
  mime_type: string | null;
  created_at: string | null;
  latest_version_no: number | null;
}

export interface ResumeListOut {
  meta: PaginatedMeta;
  items: ResumeOut[];
}

/** Resume detail, including the latest version's raw text. */
export interface ResumeDetailOut {
  id: string;
  filename: string;
  mime_type: string | null;
  storage_uri: string | null;
  created_at: string | null;
  latest_version: ResumeVersionOut | null;
}

// --- Resume-aware JD analysis (mirrors backend schemas/jd_analysis.py) ---

export type JdAnalysisSeverity = "low" | "medium" | "high";

export type JdAnalysisEvidenceSource = "jd" | "resume" | "profile";

export type JdAnalysisRecommendation =
  | "strong_match"
  | "possible_match"
  | "weak_match"
  | "not_enough_info";

export interface JdAnalysisRiskPoint {
  title: string;
  detail: string;
  severity: JdAnalysisSeverity;
}

export interface JdAnalysisEvidence {
  claim: string;
  source: JdAnalysisEvidenceSource;
  quote?: string | null;
}

/**
 * Validated structured output produced by the model gateway. Mirrors
 * `JdAnalysisModelOutput` in backend/app/schemas/jd_analysis.py.
 */
export interface JdAnalysisModelOutput {
  role_summary: string;
  responsibilities: string[];
  hard_requirements: string[];
  nice_to_have_requirements: string[];
  resume_match_evidence: JdAnalysisEvidence[];
  risk_points: JdAnalysisRiskPoint[];
  salary_note: string;
  growth_note: string;
  stability_note: string;
  match_score: number | null;
  risk_score: number | null;
  skill_gaps: string[];
  interview_preparation: string[];
  recommendation: JdAnalysisRecommendation;
}

/** Body of POST /api/v1/jobs/{job_id}/analyses. */
export interface RunJdAnalysisRequest {
  resume_version_id: string;
}

/** Outbound view of a persisted JobAnalysis row. */
export interface JobAnalysisOut {
  id: string;
  job_id: string;
  agent_run_id?: string | null;
  match_score?: number | null;
  risk_score?: number | null;
  summary?: string | null;
  salary_analysis?: Record<string, unknown> | null;
  growth_analysis?: Record<string, unknown> | null;
  stability_analysis?: Record<string, unknown> | null;
  created_at?: string | null;
}

/** Outbound view of a persisted GeneratedArtifact row. */
export interface GeneratedArtifactOut {
  id: string;
  user_id?: string | null;
  job_id?: string | null;
  resume_version_id?: string | null;
  agent_run_id?: string | null;
  artifact_type: string;
  source_ids?: Record<string, unknown> | null;
  prompt_version?: string | null;
  model_name?: string | null;
  content: string;
  created_at?: string | null;
}

/**
 * Response shape for POST /jobs/{job_id}/analyses (design.md §5.1).
 *
 * @deprecated queue-migration — retained for older callers; the enqueue-and-poll
 * endpoint now returns {@link RunJdAnalysisSubmitResponse}.
 */
export interface RunJdAnalysisResponse {
  agent_run: AgentRunOut;
  analysis: JobAnalysisOut;
  artifact: GeneratedArtifactOut;
  structured: JdAnalysisModelOutput;
}

/** Lightweight run summary surfaced in the analysis submit response. */
export interface RunJdAnalysisRunSummary {
  id: string;
  status: string;
  error?: string | null;
}

/**
 * Immediate response for POST /jobs/{job_id}/analyses (enqueue-and-poll).
 *
 * The endpoint creates a queued AgentRun and enqueues the analysis job to the
 * worker queue, then returns immediately with this response. The frontend
 * polls GET /agent-runs/{run_id}/detail until the run reaches a terminal
 * status, then hydrates the analysis from persisted rows.
 */
export interface RunJdAnalysisSubmitResponse {
  run: RunJdAnalysisRunSummary;
  resume_version_id: string;
}

/**
 * Analysis + latest artifact + re-parsed structured output. Carries enough
 * data to reconstruct the UI from persisted rows (mirrors JobAnalysisDetailOut).
 */
export interface JobAnalysisDetailOut {
  analysis: JobAnalysisOut;
  artifact: GeneratedArtifactOut | null;
  structured: JdAnalysisModelOutput | null;
}

/** Paginated list of analyses for a job (design.md §5.2). */
export interface JobAnalysisListOut {
  meta: Record<string, unknown>;
  items: JobAnalysisDetailOut[];
}

// --- Profile draft apply (resume → profile) ---

/** Body of POST /resumes/{resume_id}/versions/{version_id}/apply-profile-draft. */
export interface ApplyProfileDraftRequest {
  confirm?: boolean;
  overwrite?: boolean;
}

/** One field-level diff returned by the draft-apply endpoint. */
export interface ProfileDraftFieldDiff {
  field: string;
  current_value: unknown;
  draft_value: unknown;
  will_change: boolean;
  blocked_reason: string | null;
}

/** Response shape for the draft-apply endpoint. */
export interface ApplyProfileDraftResponse {
  applied: boolean;
  confirm: boolean;
  overwrite: boolean;
  diffs: ProfileDraftFieldDiff[];
  updated_profile: Record<string, unknown> | null;
}

// --- Applications ---

export interface ApplicationTimelineEventOut {
  id: string;
  type: string;
  at: string;
  actor: string;
  from_status: string | null;
  to_status: string | null;
  summary: string | null;
  metadata: Record<string, unknown>;
}

export interface ApplicationOut {
  id: string;
  user_id: string;
  job_id: string;
  resume_version_id: string | null;
  status: string;
  timeline: ApplicationTimelineEventOut[];
  latest_error: Record<string, unknown> | null;
  latest_agent_run_id: string | null;
  readiness_snapshot: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
  /**
   * True when the create endpoint returned an existing (duplicate) record
   * instead of creating a new one. The frontend uses this to skip
   * auto-generation of readiness artifacts on duplicates.
   */
  is_duplicate?: boolean;
}

export interface ApplicationListOut {
  meta: { page: number; page_size: number; total: number };
  items: ApplicationOut[];
}

// --- External-action approval boundary ---

export type ExternalActionType =
  | "platform_submit"
  | "hr_message"
  | "resume_upload"
  | "profile_fill"
  | "follow_up_message"
  | "boss_immediate_communicate";

export type ExternalActionStatus =
  | "draft"
  | "approval_required"
  | "approved"
  | "stale"
  | "revoked"
  | "blocked";

export interface ApprovalRecord {
  approved_by: string;
  approved_at: string;
  approved_payload_hash: string;
}

export interface ApplicationActionSourceSnapshot {
  job_id: string;
  resume_version_id: string | null;
  artifact_ids: string[];
  source_hash: string;
}

export interface ApplicationActionPreview {
  action_type: ExternalActionType;
  target_platform: string | null;
  target_resource: string | null;
  selected_artifact_ids: string[];
  outgoing_text: string | null;
  resume_file_reference: string | null;
}

export interface ApplicationActionOut {
  id: string;
  application_id: string;
  user_id: string;
  action_type: ExternalActionType;
  status: ExternalActionStatus;
  payload_preview: ApplicationActionPreview;
  payload_hash: string;
  source_snapshot: ApplicationActionSourceSnapshot;
  approval: ApprovalRecord | null;
  stale_reason: string | null;
  external_idempotency_key: string | null;
  external_started_at: string | null;
  external_completed_at: string | null;
  external_result_status: ExternalActionResultStatus | null;
  external_result: ExternalActionResult | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ApplicationActionListOut {
  items: ApplicationActionOut[];
}

export interface ApplicationActionSourceSnapshotInput {
  job_id: string;
  resume_version_id: string | null;
  artifact_ids: string[];
  source_hash: string;
}

export interface ApplicationActionCreate {
  action_type: ExternalActionType;
  target_platform: string | null;
  target_resource: string | null;
  selected_artifact_ids: string[];
  outgoing_text: string | null;
  resume_file_reference: string | null;
  source_snapshot: ApplicationActionSourceSnapshotInput;
}

/**
 * Terminal status of an executed external side effect (mirrors backend
 * ``ExternalActionResultStatus`` enum). Used by the guided-submit panel to
 * render the post-submit state of a platform action.
 */
export type ExternalActionResultStatus =
  | "submitted"
  | "duplicate"
  | "unknown"
  | "failed";

/**
 * Sanitized result metadata of an executed external side effect. The ``result``
 * dict carries only safe metadata — never raw cookies, tokens, page HTML, raw
 * resume, or raw JD (mirrors ``ExternalActionResult``).
 */
export interface ExternalActionResult {
  result_status: ExternalActionResultStatus;
  started_at: string;
  completed_at: string;
  result: Record<string, unknown>;
}

/** Back-compat alias for call sites that emphasize external execution fields. */
export type ApplicationActionOutFull = ApplicationActionOut;

// --- Platform guided-submit workflow ---

/** Classified outcome of a final submit attempt (mirrors ``SubmitOutcome``). */
export type SubmitOutcome =
  | "submitted"
  | "duplicate_detected"
  | "unknown"
  | "platform_failure";

/** Request payload for the platform guided-submit prepare endpoint. */
export interface PlatformSubmissionPrepareRequest {
  target_resource: string;
  selected_artifact_ids: string[];
  outgoing_text: string | null;
  resume_file_reference: string | null;
}

/** Immediate (enqueue-and-poll) response for the prepare endpoint. */
export interface PlatformSubmissionPrepareResponse {
  run: RunReadinessRunSummary;
  application_id: string;
  target_platform: string;
  mode: string;
}

/** Final-submit response carrying the action after the adapter ran. */
export interface PlatformSubmissionSubmitResponse {
  run: RunReadinessRunSummary;
  application_id: string;
  action: ApplicationActionOutFull;
}

/** Abort response carrying the revoked action. */
export interface PlatformSubmissionAbortResponse {
  run: RunReadinessRunSummary;
  application_id: string;
  action: ApplicationActionOutFull;
}

// --- Userscript bridge status ---

/** Response for ``GET /userscript-bridge/status``. */
export interface BridgeStatusResponse {
  connected: boolean;
  last_heartbeat: string | null;
  active_application_id: string | null;
  page_id: string | null;
  page_url_hash: string | null;
  page_title: string | null;
}

// --- Application readiness: failure envelope, artifacts, payloads ---

/** Mirrors backend ``ApplicationFailureCategory`` enum. */
export type ApplicationFailureCategory =
  | "queue"
  | "model"
  | "validation"
  | "data"
  | "user_action"
  | "platform"
  | "unknown";

/** Mirrors backend ``ApplicationFailureNextAction`` enum. */
export type ApplicationFailureNextAction =
  | "retry"
  | "edit_source"
  | "choose_resume"
  | "reapprove"
  | "manual_review";

/** Safe, sanitized representation of a failed readiness operation. */
export interface ApplicationFailureEnvelope {
  category: ApplicationFailureCategory;
  code: string;
  message: string;
  retryable: boolean;
  next_action: ApplicationFailureNextAction;
  agent_run_id: string | null;
  source_ids: Record<string, unknown>;
  occurred_at: string;
}

/** Stable metadata snapshot used to detect stale artifacts. */
export interface ApplicationSourceSnapshot {
  job_id: string;
  job_updated_at: string | null;
  resume_version_id: string | null;
  resume_version_no: number | null;
  profile_updated_at: string | null;
  prompt_versions: Record<string, string>;
  source_hash: string;
}

export interface ApplicationCreate {
  job_id: string;
  resume_version_id?: string | null;
}

export interface ApplicationStatusUpdate {
  status: string;
  note?: string | null;
  failure?: ApplicationFailureEnvelope | null;
  agent_run_id?: string | null;
}

export interface ApplicationTimelineCreate {
  summary: string;
  metadata?: Record<string, unknown>;
}

/** The four readiness artifact types the panel can generate. */
export type ReadinessArtifactType =
  | "hr_opening_message"
  | "resume_rewrite_snippet"
  | "skill_gap_plan"
  | "interview_prep";

/** Lightweight run summary returned immediately by the generate endpoint. */
export interface RunReadinessRunSummary {
  id: string;
  status: string;
  error: string | null;
}

export interface RunReadinessSubmitResponse {
  run: RunReadinessRunSummary;
  application_id: string;
  artifact_type: ReadinessArtifactType;
}

/** Outbound view of a persisted readiness ``GeneratedArtifact`` row. */
export interface ReadinessArtifactOut {
  id: string;
  user_id: string | null;
  job_id: string | null;
  resume_version_id: string | null;
  agent_run_id: string | null;
  artifact_type: string;
  source_ids: Record<string, unknown> | null;
  prompt_version: string | null;
  model_name: string | null;
  content: string;
  created_at: string | null;
}

export interface ReadinessArtifactListOut {
  items: ReadinessArtifactOut[];
}

// --- BOSS recommended-job communicate flow ---
//
// These types mirror the backend Pydantic schemas in
// ``backend/app/schemas/boss_recommended_job.py``,
// ``boss_match_decision.py``, and ``boss_communicate.py``. They back the
// RecommendedJobPilotPanel which orchestrates the inspect → match → prepare →
// approve → execute flow for BOSS immediate-communicate.

/** Outcome of a BOSS recommended-job inspect call (mirrors ``InspectStatus``). */
export type InspectStatus = "ok" | "jd_too_sparse" | "read_failed";

/** Response for ``POST /boss/recommended-jobs/current/inspect``. */
export interface InspectJobOut {
  job: JobOut | null;
  application: ApplicationOut | null;
  is_new_job: boolean;
  is_new_application: boolean;
  inspect_status: InspectStatus;
  message: string | null;
  agent_run_id: string | null;
}

/** Allowed match-decision values (mirrors ``MatchDecision``). */
export type MatchDecision = "communicate" | "skip" | "needs_review";

/** Response for ``POST /boss/recommended-jobs/{job_id}/match``. */
export interface MatchDecisionOut {
  decision: MatchDecision;
  score: number;
  reasons: string[];
  risks: string[];
  missing_requirements: string[];
  opening_message: string | null;
  job_id: string;
  agent_run_id: string | null;
  artifact_id: string | null;
  message: string | null;
}

/** Response for ``POST /boss/recommended-jobs/{job_id}/communicate/prepare``. */
export interface CommunicatePrepareOut {
  action: ApplicationActionOut;
  message: string;
}

/** Response for ``POST /boss/recommended-jobs/{job_id}/communicate/{action_id}/execute``. */
export interface CommunicateExecuteOut {
  action: ApplicationActionOut;
  message: string;
}
