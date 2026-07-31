// Centralized API client. All backend calls go through this module.

import axios from "axios";
import type {
  HealthResponse,
  JobCreate,
  JobListOut,
  JobOut,
  AgentRunOut,
  AgentRunDetailOut,
  ApplyProfileDraftRequest,
  ApplyProfileDraftResponse,
  JdParseResponse,
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

// --- JD paste parsing (parse-then-create) ---

/**
 * Parse raw JD text into structured draft fields via the model gateway. Model
 * failures are recoverable: the endpoint returns HTTP 200 with a failed run
 * and empty typed fields so the caller can fall back to manual entry.
 */
export async function parseJobJd(
  rawJd: string,
  platform?: string,
): Promise<JdParseResponse> {
  const { data } = await apiClient.post<JdParseResponse>("/jobs/parse", {
    raw_jd: rawJd,
    ...(platform ? { platform } : {}),
  });
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
  // multipart boundary.
  const { data } = await apiClient.post<ResumeDetailOut>("/resumes", form, {
    headers: { "Content-Type": "multipart/form-data" },
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

/** Re-run structured fact extraction on an existing resume version. */
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
    return err.message;
  }
  return "请求失败，请稍后重试";
}
