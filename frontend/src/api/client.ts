// Centralized API client. All backend calls go through this module.

import axios, { AxiosError } from "axios";
import type {
  HealthResponse,
  JobCreate,
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
  RunJdAnalysisResponse,
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

// --- JD paste parsing (parse-then-create, enqueue-and-poll) ---

/**
 * Submit raw JD text for asynchronous parsing. The endpoint creates a
 * ``queued`` AgentRun, enqueues a worker job, and returns immediately with
 * HTTP 202. Poll ``getAgentRunDetail(run.id)`` until terminal status, then
 * hydrate fields from ``result.fields``.
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
): Promise<RunJdAnalysisResponse> {
  const { data } = await apiClient.post<RunJdAnalysisResponse>(
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
