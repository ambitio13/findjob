// Centralized API client. All backend calls go through this module.

import axios from "axios";
import type {
  HealthResponse,
  JobCreate,
  JobListOut,
  JobOut,
  ManualJdAnalysisDemoResponse,
  AgentRunOut,
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
): Promise<{ meta: { page: number; page_size: number; total: number }; items: AgentRunOut[] }> {
  const { data } = await apiClient.get("/agent-runs", {
    params: { page, page_size: pageSize },
  });
  return data;
}

export async function getAgentRun(id: string): Promise<AgentRunOut> {
  const { data } = await apiClient.get<AgentRunOut>(`/agent-runs/${id}`);
  return data;
}

export async function runManualJdAnalysisDemo(
  jdText: string,
): Promise<ManualJdAnalysisDemoResponse> {
  const { data } = await apiClient.post<ManualJdAnalysisDemoResponse>(
    "/agent-runs/manual-jd-analysis-demo",
    { jd_text: jdText },
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
