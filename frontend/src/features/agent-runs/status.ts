/**
 * Shared agent-run status domain.
 *
 * The backend ``AgentRun.status`` / ``AgentStep.status`` columns are plain
 * strings, but the values form a small finite set. Declaring them as a union
 * here lets the frontend share status sets, color maps, and display labels
 * across every workflow (JD parse, resume extraction, JD analysis) instead of
 * duplicating them per page.
 *
 * The values mirror the backend ``AgentRunStatus`` enum and the
 * ``ResumeExtractionStatus`` superset.
 */

/** Lifecycle status of an agent run or step. */
export type AgentRunStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "not_run";

/** Statuses that mark a run as finished — polling can stop. */
export const TERMINAL_AGENT_RUN_STATUSES: ReadonlySet<AgentRunStatus> =
  new Set(["succeeded", "failed", "not_run"]);

/** Statuses that indicate an active (in-flight) run. */
export const ACTIVE_AGENT_RUN_STATUSES: ReadonlySet<AgentRunStatus> = new Set([
  "queued",
  "running",
]);

/**
 * Terminal statuses shared by JD parse and JD analysis polling. Kept as a
 * string set for compatibility with the loose ``AgentRunOut.status: string``
 * type and with older callers that pass raw strings.
 *
 * @deprecated use {@link TERMINAL_AGENT_RUN_STATUSES} in new code.
 */
export const TERMINAL_JD_PARSE_STATUSES: ReadonlySet<string> = new Set([
  "succeeded",
  "failed",
]);

/** Display label for each agent-run status. */
export const AGENT_RUN_STATUS_LABEL: Record<AgentRunStatus, string> = {
  queued: "排队中",
  running: "运行中",
  succeeded: "已完成",
  failed: "已失败",
  not_run: "未运行",
};

/** Ant Design Tag color for each agent-run status. */
export const AGENT_RUN_STATUS_COLOR: Record<AgentRunStatus, string> = {
  queued: "default",
  running: "processing",
  succeeded: "success",
  failed: "error",
  not_run: "default",
};

/**
 * Coerce an unknown status string into the typed union, falling back to
 * ``not_run`` for unrecognized values so the UI never crashes.
 */
export function normalizeAgentRunStatus(
  status: string | null | undefined,
): AgentRunStatus {
  if (status && status in AGENT_RUN_STATUS_LABEL) {
    return status as AgentRunStatus;
  }
  return "not_run";
}

/** Default polling interval for non-terminal agent-run status (milliseconds). */
export const AGENT_RUN_POLL_INTERVAL_MS = 3000;
