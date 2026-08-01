/**
 * Shared copy for async agent-run workflows.
 *
 * Centralizes user-facing messages for queued / running / succeeded / failed
 * states and retry actions so the three workflow surfaces (JD paste modal,
 * resume detail, job detail) use consistent wording per the UX spec.
 */

/** Progress message shown while a run is queued or running. */
export function asyncRunProgressMessage(workflowLabel: string): string {
  return `已提交${workflowLabel}任务，正在轮询运行状态…`;
}

/** Success message shown when a run reaches ``succeeded``. */
export function asyncRunSuccessMessage(workflowLabel: string): string {
  return `${workflowLabel}完成`;
}

/**
 * Failure message shown when a run reaches ``failed``. The error string is
 * sanitized by the backend; if absent, a generic fallback is used.
 */
export function asyncRunFailureMessage(
  workflowLabel: string,
  error?: string | null,
): string {
  const reason = error ?? "未知原因";
  return `${workflowLabel}未成功（${reason}），可重试或检查运行轨迹`;
}

/**
 * Retry button label. Takes a verb so callers can say "重新解析" vs "重新分析"
 * while keeping the same structure.
 */
export function asyncRetryLabel(verb: string): string {
  return `重新${verb}`;
}

/** Label for the run-id hint shown in progress / failure surfaces. */
export function runIdHint(runId: string): string {
  return `运行 ID: ${runId}`;
}
