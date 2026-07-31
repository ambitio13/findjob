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
  | "succeeded"
  | "failed"
  | "needs_confirmation"
  | "not_run";

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

/** Response shape for POST /jobs/{job_id}/analyses (design.md §5.1). */
export interface RunJdAnalysisResponse {
  agent_run: AgentRunOut;
  analysis: JobAnalysisOut;
  artifact: GeneratedArtifactOut;
  structured: JdAnalysisModelOutput;
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
