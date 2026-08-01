import {
  Alert,
  Button,
  Card,
  Descriptions,
  Space,
  Tag,
  Typography,
} from "antd";
import type { ApplicationFailureEnvelope, ApplicationFailureNextAction } from "@/types";

const { Text, Paragraph } = Typography;

const CATEGORY_LABEL: Record<string, string> = {
  queue: "队列",
  model: "模型",
  validation: "校验",
  data: "数据",
  user_action: "用户操作",
  platform: "平台",
  unknown: "未知",
};

const NEXT_ACTION_LABEL: Record<ApplicationFailureNextAction, string> = {
  retry: "重试",
  edit_source: "修改来源",
  choose_resume: "更换简历",
  reapprove: "重新审批",
  manual_review: "人工排查",
};

interface Props {
  /** The sanitized failure envelope from ``ApplicationOut.latest_error``. */
  failure: ApplicationFailureEnvelope;
  /** Called when the user clicks the next-action button. */
  onNextAction?: (next: ApplicationFailureNextAction) => void;
  /** Whether an action (e.g. retry) is currently in flight. */
  acting?: boolean;
}

/**
 * Renders a failed readiness operation using the shared failure envelope.
 *
 * Per design.md the panel surfaces: category/code, safe message, retryability,
 * the concrete next action, a link to the agent run, and a source-metadata
 * summary. It never shows raw text or secrets — only the sanitized fields the
 * backend persisted.
 */
export function FailurePanel({ failure, onNextAction, acting }: Props) {
  const next = failure.next_action;
  const showButton = onNextAction && next !== "manual_review";

  return (
    <Card type="inner" title="最近失败" size="small">
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Descriptions column={2} size="small" bordered>
          <Descriptions.Item label="类别">
            <Tag color="volcano">
              {CATEGORY_LABEL[failure.category] ?? failure.category}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="错误码">
            <Text code>{failure.code}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="可重试" span={2}>
            <Tag color={failure.retryable ? "green" : "default"}>
              {failure.retryable ? "可重试" : "不可重试"}
            </Tag>
            <Text type="secondary" style={{ marginLeft: 8 }}>
              建议操作：{NEXT_ACTION_LABEL[next]}
            </Text>
          </Descriptions.Item>
        </Descriptions>

        <Paragraph style={{ marginBottom: 0 }}>{failure.message}</Paragraph>

        <Descriptions column={1} size="small">
          {failure.agent_run_id ? (
            <Descriptions.Item label="运行 ID">
              <Text code>{failure.agent_run_id}</Text>
            </Descriptions.Item>
          ) : null}
          <Descriptions.Item label="发生时间">
            {formatTime(failure.occurred_at)}
          </Descriptions.Item>
          {Object.keys(failure.source_ids).length > 0 ? (
            <Descriptions.Item label="来源摘要">
              <Text code style={{ fontSize: 12 }}>
                {formatSourceIds(failure.source_ids)}
              </Text>
            </Descriptions.Item>
          ) : null}
        </Descriptions>

        {showButton ? (
          <Space>
            <Button
              type="primary"
              loading={acting}
              onClick={() => onNextAction?.(next)}
            >
              {NEXT_ACTION_LABEL[next]}
            </Button>
          </Space>
        ) : (
          <Alert
            type="warning"
            showIcon
            message="此失败需人工排查"
            description="系统无法自动恢复，请检查运行轨迹或来源数据后手动处理。"
          />
        )}
      </Space>
    </Card>
  );
}

function formatTime(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

/** Render the sanitized source_ids dict compactly (IDs only, no raw text). */
function formatSourceIds(ids: Record<string, unknown>): string {
  const safe: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(ids)) {
    if (typeof v === "string" || typeof v === "number") safe[k] = v;
  }
  try {
    return JSON.stringify(safe);
  } catch {
    return "";
  }
}
