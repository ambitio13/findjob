import { Tag } from "antd";
import type { AgentRunStatus } from "./status";
import {
  AGENT_RUN_STATUS_COLOR,
  AGENT_RUN_STATUS_LABEL,
  normalizeAgentRunStatus,
} from "./status";

/**
 * Shared status tag for agent runs.
 *
 * Renders the canonical color + label for every workflow (JD parse, resume
 * extraction, JD analysis) so status rendering stays in one place per the
 * type-safety spec's display-mapping rule.
 */
export function AgentRunStatusTag({
  status,
}: {
  status: string | null | undefined;
}) {
  const normalized: AgentRunStatus = normalizeAgentRunStatus(status);
  return (
    <Tag color={AGENT_RUN_STATUS_COLOR[normalized]}>
      {AGENT_RUN_STATUS_LABEL[normalized]}
    </Tag>
  );
}
