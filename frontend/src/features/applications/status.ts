/**
 * Shared application-readiness status domain.
 *
 * Mirrors the backend ``ApplicationStatus`` enum and the transition table in
 * ``app.services.application_state.TRANSITIONS``. Keeping the labels, colors,
 * and allowed transitions in one place lets the readiness panel, summary, and
 * action buttons stay consistent without duplicating maps.
 */

/** The ten application lifecycle statuses (backend ``ApplicationStatus``). */
export type ApplicationStatus =
  | "planned"
  | "preparing"
  | "materials_ready"
  | "approval_required"
  | "approved"
  | "submitted"
  | "failed"
  | "paused"
  | "rejected"
  | "interviewing";

/** Ant Design Tag color for each application status. */
export const APP_STATUS_COLOR: Record<ApplicationStatus, string> = {
  planned: "default",
  preparing: "blue",
  materials_ready: "cyan",
  approval_required: "orange",
  approved: "geekblue",
  submitted: "green",
  failed: "volcano",
  paused: "default",
  rejected: "red",
  interviewing: "purple",
};

/** Chinese display label for each application status. */
export const APP_STATUS_LABEL: Record<ApplicationStatus, string> = {
  planned: "计划中",
  preparing: "准备中",
  materials_ready: "材料就绪",
  approval_required: "待审批",
  approved: "已批准",
  submitted: "已投递",
  failed: "失败",
  paused: "已暂停",
  rejected: "已拒绝",
  interviewing: "面试中",
};

/**
 * Allowed status transitions, mirroring the backend
 * ``TRANSITIONS`` table. Used by the action buttons to enable/disable
 * transitions client-side (the backend enforces the final truth).
 */
export const APP_TRANSITIONS: Record<ApplicationStatus, ReadonlySet<ApplicationStatus>> = {
  planned: new Set<ApplicationStatus>(["preparing", "paused"]),
  preparing: new Set<ApplicationStatus>(["materials_ready", "failed", "paused"]),
  failed: new Set<ApplicationStatus>(["preparing", "paused"]),
  materials_ready: new Set<ApplicationStatus>([
    "approval_required",
    "preparing",
    "paused",
  ]),
  approval_required: new Set<ApplicationStatus>(["approved", "preparing", "paused"]),
  approved: new Set<ApplicationStatus>([
    "submitted",
    "approval_required",
    "failed",
    "paused",
  ]),
  submitted: new Set<ApplicationStatus>(["interviewing", "rejected", "paused"]),
  paused: new Set<ApplicationStatus>(["planned", "preparing"]),
  interviewing: new Set<ApplicationStatus>(["rejected", "paused"]),
  rejected: new Set<ApplicationStatus>(["planned"]),
};

/** Return ``true`` if ``from -> to`` is an allowed transition. */
export function canTransition(
  from: ApplicationStatus,
  to: ApplicationStatus,
): boolean {
  return APP_TRANSITIONS[from]?.has(to) ?? false;
}

/** Coerce an unknown status string into the typed union (fallback: planned). */
export function normalizeApplicationStatus(
  status: string | null | undefined,
): ApplicationStatus {
  if (status && status in APP_STATUS_LABEL) {
    return status as ApplicationStatus;
  }
  return "planned";
}

/** Timeline event type → Ant Design Timeline color. */
export const TIMELINE_COLOR: Record<string, string> = {
  created: "blue",
  status_changed: "blue",
  user_note: "gray",
  failure: "red",
  action_previewed: "orange",
  action_approved: "green",
  action_revoked: "red",
  action_stale: "volcano",
};

/** Chinese label for each readiness artifact type. */
export const ARTIFACT_TYPE_LABEL: Record<string, string> = {
  hr_opening_message: "HR 开场话术",
  resume_rewrite_snippet: "简历改写片段",
  skill_gap_plan: "技能差距计划",
  interview_prep: "面试准备",
};
