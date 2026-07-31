// Centralized API client. All backend calls go through this module.

import axios from "axios";
import type {
  HealthResponse,
  JobCreate,
  JobListOut,
  JobOut,
  AgentRunOut,
  AgentRunDetailOut,
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
