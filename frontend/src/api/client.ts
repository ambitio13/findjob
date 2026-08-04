// Centralized API client. All backend calls go through this module.

import axios, { AxiosError } from "axios";
import type {
  HealthResponse,
  JobCreate,
  JobUpdate,
  JobListOut,
  JobOut,
  AgentRunOut,
  AgentRunDetailOut,
  ApplyProfileDraftRequest,
  ApplyProfileDraftResponse,
  JdParseSubmitResponse,
  ResumeDetailOut,
  ResumeListOut,
  ResumeVersionListItem,
  UserProfile,
  UserProfileUpdate,
  JobAnalysisListOut,
  RunJdAnalysisSubmitResponse,
  ApplicationListOut,
  ApplicationOut,
  ApplicationCreate,
  ApplicationStatusUpdate,
  ApplicationTimelineCreate,
  ReadinessArtifactType,
  ReadinessArtifactListOut,
  RunReadinessSubmitResponse,
  ApplicationActionCreate,
  ApplicationActionListOut,
  ApplicationActionOut,
  PlatformSubmissionAbortResponse,
  PlatformSubmissionPrepareRequest,
  PlatformSubmissionPrepareResponse,
  PlatformSubmissionSubmitResponse,
  BridgeStatusResponse,
  InspectJobOut,
  MatchDecisionOut,
  CommunicatePrepareOut,
  CommunicateExecuteOut,
} from "@/types";

const baseURL = "/api/v1";

export const apiClient = axios.create({
  baseURL,
  timeout: 15000,
  headers: { "Content-Type": "application/json" },
});

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await apiClient.get<HealthResponse>("/health");
  return data;
}

export async function listJobs(page = 1, pageSize = 20): Promise<JobListOut> {
  const { data } = await apiClient.get<JobListOut>("/jobs", {
    params: { page, page_size: pageSize },
  });
  return data;
}

export async function getJob(id: string): Promise<JobOut> {
  const { data } = await apiClient.get<JobOut>(`/jobs/${id}`);
  return data;
}

export async function createJob(payload: JobCreate): Promise<JobOut> {
  const { data } = await apiClient.post<JobOut>("/jobs", payload);
  return data;
}

/**
 * Partially update an owned job (company/title/location/salary/direction/
 * platform/jd_raw). Used to correct a parsed draft or replace the
 * `(解析中…)` placeholder after an async parse completes.
 */
export async function updateJob(
  id: string,
  payload: JobUpdate,
): Promise<JobOut> {
  // Strip ``undefined`` values so axios does not serialize omitted keys. The
  // backend distinguishes "omitted" (no-op) from "explicit null" (clear) via
  // ``model_fields_set``; sending ``undefined`` would otherwise be dropped by
  // JSON.stringify and treated as omitted, which is what we want for keys the
  // form did not populate. Explicit ``null`` is preserved to clear a column.
  const body = Object.fromEntries(
    Object.entries(payload).filter(([, v]) => v !== undefined),
  );
  const { data } = await apiClient.patch<JobOut>(`/jobs/${id}`, body);
  return data;
}

// --- JD paste parsing (create-job-first, enqueue-and-poll) ---

/**
 * Submit raw JD text for asynchronous parsing. The endpoint creates a
 * `JobPosting` row up front (company/title placeholder) plus a `queued`
 * AgentRun, enqueues a worker job, and returns immediately with HTTP 202.
 * The frontend can close the create modal right away and show async progress
 * in the job list. Poll the job row (or the run detail) until the run reaches
 * a terminal status; on success the worker has written the parsed draft into
 * `job.jd_normalized` and overwritten the placeholder company/title.
 */
export async function parseJobJd(
  rawJd: string,
  platform?: string,
): Promise<JdParseSubmitResponse> {
  const { data } = await apiClient.post<JdParseSubmitResponse>(
    "/jobs/parse",
    {
      raw_jd: rawJd,
      ...(platform ? { platform } : {}),
    },
  );
  return data;
}

export async function listAgentRuns(
  page = 1,
  pageSize = 20,
  jobId?: string,
  workflowType?: string,
): Promise<{ meta: { page: number; page_size: number; total: number }; items: AgentRunOut[] }> {
  const { data } = await apiClient.get("/agent-runs", {
    params: {
      page,
      page_size: pageSize,
      ...(jobId ? { job_id: jobId } : {}),
      ...(workflowType ? { workflow_type: workflowType } : {}),
    },
  });
  return data;
}

export async function getAgentRun(id: string): Promise<AgentRunOut> {
  const { data } = await apiClient.get<AgentRunOut>(`/agent-runs/${id}`);
  return data;
}

/** Fetch a run's full detail, including the ordered sanitized step trail. */
export async function getAgentRunDetail(
  id: string,
): Promise<AgentRunDetailOut> {
  const { data } = await apiClient.get<AgentRunDetailOut>(
    `/agent-runs/${id}/detail`,
  );
  return data;
}

// --- Current user profile ---

export async function getCurrentUser(): Promise<UserProfile> {
  const { data } = await apiClient.get<UserProfile>("/users/me");
  return data;
}

export async function updateCurrentUser(
  payload: UserProfileUpdate,
): Promise<UserProfile> {
  const { data } = await apiClient.patch<UserProfile>("/users/me", payload);
  return data;
}

// --- Resumes ---

export async function uploadResume(file: File): Promise<ResumeDetailOut> {
  const form = new FormData();
  form.append("file", file);
  // Override the instance-level JSON content type so the browser sets the
  // multipart boundary. Use a longer per-request timeout for uploads: even with
  // extraction decoupled, large PDF/DOCX parsing + file persistence can still
  // exceed the default 15s budget. This is a per-endpoint timeout, NOT the fix
  // for the old inline-extraction timeout (extraction now runs in the
  // background). The global axios timeout stays at 15s for other endpoints.
  const { data } = await apiClient.post<ResumeDetailOut>("/resumes", form, {
    headers: { "Content-Type": "multipart/form-data" },
    timeout: 60_000,
  });
  return data;
}

export async function listResumes(page = 1, pageSize = 20): Promise<ResumeListOut> {
  const { data } = await apiClient.get<ResumeListOut>("/resumes", {
    params: { page, page_size: pageSize },
  });
  return data;
}

export async function getResume(id: string): Promise<ResumeDetailOut> {
  const { data } = await apiClient.get<ResumeDetailOut>(`/resumes/${id}`);
  return data;
}

export async function listResumeVersions(
  resumeId: string,
): Promise<ResumeVersionListItem[]> {
  const { data } = await apiClient.get<ResumeVersionListItem[]>(
    `/resumes/${resumeId}/versions`,
  );
  return data;
}

/** Re-run structured fact extraction on an existing resume version.
 *
 * The backend enqueues the extraction and returns immediately with a
 * `ResumeDetailOut` whose `_extraction.status` is `pending` (or `failed` if
 * enqueue itself failed). The caller should poll `GET /resumes/{id}` until a
 * terminal status is reached.
 */
export async function reextractResumeFacts(
  resumeId: string,
  versionId: string,
): Promise<ResumeDetailOut> {
  const { data } = await apiClient.post<ResumeDetailOut>(
    `/resumes/${resumeId}/versions/${versionId}/extract`,
  );
  return data;
}

/**
 * Preview (confirm=false) or apply (confirm=true) a resume version's
 * extracted facts as profile updates. Returns a field-level diff so the
 * UI can show what will change before the user confirms.
 */
export async function applyProfileDraft(
  resumeId: string,
  versionId: string,
  payload: ApplyProfileDraftRequest,
): Promise<ApplyProfileDraftResponse> {
  const { data } = await apiClient.post<ApplyProfileDraftResponse>(
    `/resumes/${resumeId}/versions/${versionId}/apply-profile-draft`,
    payload,
  );
  return data;
}

// --- Resume-aware JD analysis ---

export async function runJdAnalysis(
  jobId: string,
  resumeVersionId: string,
): Promise<RunJdAnalysisSubmitResponse> {
  const { data } = await apiClient.post<RunJdAnalysisSubmitResponse>(
    `/jobs/${jobId}/analyses`,
    { resume_version_id: resumeVersionId },
  );
  return data;
}

export async function listJobAnalyses(
  jobId: string,
  page = 1,
  pageSize = 20,
): Promise<JobAnalysisListOut> {
  const { data } = await apiClient.get<JobAnalysisListOut>(
    `/jobs/${jobId}/analyses`,
    {
      params: { page, page_size: pageSize },
    },
  );
  return data;
}

// --- Applications ---

export async function listApplications(
  page = 1,
  pageSize = 20,
  status?: string,
): Promise<ApplicationListOut> {
  const { data } = await apiClient.get<ApplicationListOut>("/applications", {
    params: { page, page_size: pageSize, ...(status ? { status } : {}) },
  });
  return data;
}

export async function getApplication(id: string): Promise<ApplicationOut> {
  const { data } = await apiClient.get<ApplicationOut>(`/applications/${id}`);
  return data;
}

/** Create a new application record for a job (optionally binding a resume). */
export async function createApplication(
  payload: ApplicationCreate,
): Promise<ApplicationOut> {
  const { data } = await apiClient.post<ApplicationOut>("/applications", payload);
  return data;
}

/** Apply a status transition, appending a timeline entry + failure envelope. */
export async function updateApplicationStatus(
  id: string,
  payload: ApplicationStatusUpdate,
): Promise<ApplicationOut> {
  const { data } = await apiClient.patch<ApplicationOut>(
    `/applications/${id}/status`,
    payload,
  );
  return data;
}

/** Append a manual user note to the application timeline. */
export async function appendTimelineNote(
  id: string,
  payload: ApplicationTimelineCreate,
): Promise<ApplicationOut> {
  const { data } = await apiClient.post<ApplicationOut>(
    `/applications/${id}/timeline`,
    payload,
  );
  return data;
}

/** Enqueue readiness artifact generation; returns the run summary for polling. */
export async function generateReadinessArtifact(
  applicationId: string,
  artifactType: ReadinessArtifactType,
): Promise<RunReadinessSubmitResponse> {
  const { data } = await apiClient.post<RunReadinessSubmitResponse>(
    `/applications/${applicationId}/artifacts/${artifactType}/generate`,
  );
  return data;
}

/** List persisted readiness artifacts for an application (newest first). */
export async function listApplicationArtifacts(
  applicationId: string,
): Promise<ReadinessArtifactListOut> {
  const { data } = await apiClient.get<ReadinessArtifactListOut>(
    `/applications/${applicationId}/artifacts`,
  );
  return data;
}

// --- Approval boundary (planned external actions) ---
//
// These functions let the user preview, approve, revoke, and inspect planned
// external actions. They deliberately do NOT include an execute/submit call —
// the backend exposes no such endpoint yet and future platform tools must pass
// the approval-boundary guard before touching any platform.

export async function previewApplicationAction(
  applicationId: string,
  payload: ApplicationActionCreate,
): Promise<ApplicationActionOut> {
  const { data } = await apiClient.post<ApplicationActionOut>(
    `/applications/${applicationId}/actions/preview`,
    payload,
  );
  return data;
}

export async function approveApplicationAction(
  applicationId: string,
  actionId: string,
): Promise<ApplicationActionOut> {
  const { data } = await apiClient.post<ApplicationActionOut>(
    `/applications/${applicationId}/actions/${actionId}/approve`,
  );
  return data;
}

export async function revokeApplicationAction(
  applicationId: string,
  actionId: string,
): Promise<ApplicationActionOut> {
  const { data } = await apiClient.post<ApplicationActionOut>(
    `/applications/${applicationId}/actions/${actionId}/revoke`,
  );
  return data;
}

export async function getApplicationAction(
  applicationId: string,
  actionId: string,
): Promise<ApplicationActionOut> {
  const { data } = await apiClient.get<ApplicationActionOut>(
    `/applications/${applicationId}/actions/${actionId}`,
  );
  return data;
}

export async function listApplicationActions(
  applicationId: string,
): Promise<ApplicationActionListOut> {
  const { data } = await apiClient.get<ApplicationActionListOut>(
    `/applications/${applicationId}/actions`,
  );
  return data;
}

// --- Platform guided-submit workflow ---
//
// The guided-submit flow is a two-phase, approval-gated workflow. Phase 1
// (prepare) runs the platform adapter in dry-run/fill-only mode via an agent
// run the frontend polls to completion. Phase 2 (submit) runs synchronously
// behind the approval + idempotency guards. The frontend never sees raw
// cookies, tokens, credentials, or page HTML — only the sanitized filled
// preview and approval state.

/** Start a platform guided-submit prepare run (enqueue-and-poll). */
export async function preparePlatformSubmission(
  applicationId: string,
  payload: PlatformSubmissionPrepareRequest,
): Promise<PlatformSubmissionPrepareResponse> {
  const { data } = await apiClient.post<PlatformSubmissionPrepareResponse>(
    `/applications/${applicationId}/platform-submissions/prepare`,
    payload,
  );
  return data;
}

/** Execute the final platform submit behind the approval + idempotency guards. */
export async function submitPlatformSubmission(
  applicationId: string,
  runId: string,
): Promise<PlatformSubmissionSubmitResponse> {
  const { data } = await apiClient.post<PlatformSubmissionSubmitResponse>(
    `/applications/${applicationId}/platform-submissions/${runId}/submit`,
  );
  return data;
}

/** Abort an in-flight platform final submit (side-effect-free cancellation). */
export async function abortPlatformSubmission(
  applicationId: string,
  runId: string,
): Promise<PlatformSubmissionAbortResponse> {
  const { data } = await apiClient.post<PlatformSubmissionAbortResponse>(
    `/applications/${applicationId}/platform-submissions/${runId}/abort`,
  );
  return data;
}

// --- Userscript bridge status ---
//
// The bridge endpoints are unauthenticated (the Tampermonkey userscript cannot
// send X-User-Id). The frontend polls status to show whether a userscript is
// connected before the user starts a guided submit.

/** Check whether a Tampermonkey userscript is connected to the bridge. */
export async function getBridgeStatus(): Promise<BridgeStatusResponse> {
  const { data } = await apiClient.get<BridgeStatusResponse>(
    "/userscript-bridge/status",
  );
  return data;
}

// --- BOSS recommended-job communicate flow ---
//
// These functions drive the RecommendedJobPilotPanel, which orchestrates the
// inspect → match → prepare → approve → execute flow for BOSS immediate-
// communicate. Each endpoint operates on exactly one job/application — there
// are no batch paths (the backend enforces this invariant).

/**
 * Inspect the JD on the user's active BOSS browser tab.
 *
 * Calls ``POST /boss/recommended-jobs/current/inspect``, which reads the JD
 * via the userscript bridge and upserts it into a JobPosting (+ApplicationRecord
 * when ``resumeVersionId`` is supplied).
 */
export async function inspectCurrentJob(
  resumeVersionId: string | null,
): Promise<InspectJobOut> {
  const { data } = await apiClient.post<InspectJobOut>(
    "/boss/recommended-jobs/current/inspect",
    { resume_version_id: resumeVersionId },
    // Inspect waits for the userscript to read the JD, which can exceed the
    // default 15s budget under background-tab throttling.
    { timeout: 120_000 },
  );
  return data;
}

/**
 * Run the match-decision model for a BOSS recommended job.
 *
 * Calls ``POST /boss/recommended-jobs/{jobId}/match``. This is a pure model
 * call — no browser side effect. The safety gate may downgrade a
 * ``communicate`` decision to ``needs_review``.
 */
export async function matchJob(
  jobId: string,
  resumeVersionId: string,
): Promise<MatchDecisionOut> {
  const { data } = await apiClient.post<MatchDecisionOut>(
    `/boss/recommended-jobs/${jobId}/match`,
    { resume_version_id: resumeVersionId },
    // The model call can take longer than the default 15s.
    { timeout: 60_000 },
  );
  return data;
}

/**
 * Draft a ``boss_immediate_communicate`` action in ``approval_required`` status.
 *
 * Calls ``POST /boss/recommended-jobs/{jobId}/communicate/prepare``. No browser
 * side effect — this only reads the persisted match artifact and creates an
 * ApplicationAction row.
 */
export async function prepareCommunicate(
  jobId: string,
  resumeVersionId: string,
  matchArtifactId: string,
): Promise<CommunicatePrepareOut> {
  const { data } = await apiClient.post<CommunicatePrepareOut>(
    `/boss/recommended-jobs/${jobId}/communicate/prepare`,
    {
      resume_version_id: resumeVersionId,
      match_artifact_id: matchArtifactId,
    },
  );
  return data;
}

/**
 * Execute the approved communicate action (click "立即沟通" + send opening).
 *
 * Calls ``POST /boss/recommended-jobs/{jobId}/communicate/{actionId}/execute``.
 * The action must be ``approved`` first via {@link approveApplicationAction}.
 * Returns the terminal result (submitted / duplicate / unknown / failed).
 */
export async function executeCommunicate(
  jobId: string,
  actionId: string,
  applicationId: string,
): Promise<CommunicateExecuteOut> {
  const { data } = await apiClient.post<CommunicateExecuteOut>(
    `/boss/recommended-jobs/${jobId}/communicate/${actionId}/execute`,
    { application_id: applicationId },
    // Execute waits for the userscript to click + send + read markers, which
    // can exceed the default 15s budget under background-tab throttling.
    { timeout: 120_000 },
  );
  return data;
}

// Normalize API errors into a readable message without exposing internals.
export function apiErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === "string") return detail;
    // Axios timeouts surface as `ECONNABORTED` with a raw `timeout of Nms
    // exceeded` message. For upload specifically the resume may still have
    // been saved on the backend, so normalize to copy that suggests checking
    // the list rather than exposing the raw axios wording.
    if (isAxiosTimeout(err)) {
      return "请求超时，请稍后刷新列表确认数据是否已保存";
    }
    return err.message;
  }
  return "请求失败，请稍后重试";
}

/** Detect an axios timeout error (ECONNABORTED + `timeout` code/message). */
export function isAxiosTimeout(err: AxiosError): boolean {
  if (err.code === "ECONNABORTED") return true;
  const msg = err.message || "";
  return msg.toLowerCase().includes("timeout");
}
