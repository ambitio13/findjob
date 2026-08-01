import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  List,
  message,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  apiErrorMessage,
  generateReadinessArtifact,
  listApplicationArtifacts,
} from "@/api/client";
import { useAgentRunPolling } from "@/features/agent-runs/useAgentRunPolling";
import { AgentRunStatusTag } from "@/features/agent-runs/AgentRunStatusTag";
import {
  asyncRunFailureMessage,
  asyncRunProgressMessage,
  runIdHint,
} from "@/features/agent-runs/copy";
import { TERMINAL_AGENT_RUN_STATUSES, type AgentRunStatus } from "@/features/agent-runs/status";
import type {
  ApplicationOut,
  ReadinessArtifactOut,
  ReadinessArtifactType,
} from "@/types";
import { ARTIFACT_TYPE_LABEL } from "./status";

const { Paragraph, Text } = Typography;

const ARTIFACT_TYPES: ReadinessArtifactType[] = [
  "hr_opening_message",
  "resume_rewrite_snippet",
  "skill_gap_plan",
  "interview_prep",
];

const WORKFLOW_LABEL = "生成";

interface Props {
  application: ApplicationOut;
  /** Called after a terminal run so the parent can refresh application detail. */
  onTerminal: () => void;
  /** Whether the bound resume version is missing (generates disabled). */
  resumeMissing: boolean;
  /**
   * Called whenever the artifact list or generating flag changes, so the
   * parent (ApplicationsPage) can compute staleness and feed the summary.
   */
  onStateChange?: (state: {
    artifacts: ReadinessArtifactOut[];
    generating: boolean;
  }) => void;
}

/**
 * Checklist + generator for the four readiness artifact types.
 *
 * For each type the panel shows the latest persisted artifact (or an empty
 * slot), a generate/retry button, and the active run status when polling.
 * Reuses the shared ``useAgentRunPolling`` hook so polling/cleanup logic is
 * identical to the JD analysis and resume extraction workflows.
 */
export function ArtifactChecklist({
  application,
  onTerminal,
  resumeMissing,
  onStateChange,
}: Props) {
  const [artifacts, setArtifacts] = useState<ReadinessArtifactOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedType, setSelectedType] = useState<ReadinessArtifactType>(
    "hr_opening_message",
  );
  const [pollRunId, setPollRunId] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  // Lift artifact + generating state to the parent so it can compute
  // staleness (source-hash comparison) and feed the ReadinessSummary.
  useEffect(() => {
    onStateChange?.({ artifacts, generating });
  }, [artifacts, generating, onStateChange]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listApplicationArtifacts(application.id);
      setArtifacts(data.items);
    } catch (err) {
      messageApi.warning(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [application.id, messageApi]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Poll the active generation run. On terminal status, refresh artifacts and
  // notify the parent so the application detail (snapshot/timeline) updates.
  useAgentRunPolling(pollRunId, {
    onTerminal: (detail) => {
      setGenerating(false);
      setPollRunId(null);
      if (detail.status === "succeeded") {
        messageApi.success(`${ARTIFACT_TYPE_LABEL[selectedType] ?? WORKFLOW_LABEL}生成完成`);
      } else {
        messageApi.warning(
          asyncRunFailureMessage(
            ARTIFACT_TYPE_LABEL[selectedType] ?? WORKFLOW_LABEL,
            detail.error,
          ),
        );
      }
      void refresh();
      onTerminal();
    },
  });

  const latestByType = useMemo(() => {
    const map = new Map<string, ReadinessArtifactOut>();
    for (const a of artifacts) {
      // listApplicationArtifacts is newest-first, so keep the first seen.
      if (!map.has(a.artifact_type)) map.set(a.artifact_type, a);
    }
    return map;
  }, [artifacts]);

  const handleGenerate = async (type: ReadinessArtifactType) => {
    if (resumeMissing) {
      messageApi.error("尚未绑定简历版本，无法生成材料");
      return;
    }
    setGenerating(true);
    setSelectedType(type);
    try {
      const res = await generateReadinessArtifact(application.id, type);
      // If enqueue itself failed (Redis down), the backend flips to failed.
      if (TERMINAL_AGENT_RUN_STATUSES.has(res.run.status as AgentRunStatus)) {
        setGenerating(false);
        if (res.run.status === "succeeded") {
          messageApi.success(`${ARTIFACT_TYPE_LABEL[type]}生成完成`);
        } else {
          messageApi.warning(
            asyncRunFailureMessage(ARTIFACT_TYPE_LABEL[type], res.run.error),
          );
        }
        void refresh();
        onTerminal();
        return;
      }
      messageApi.info(asyncRunProgressMessage(ARTIFACT_TYPE_LABEL[type] ?? WORKFLOW_LABEL));
      setPollRunId(res.run.id);
    } catch (err) {
      setGenerating(false);
      messageApi.error(apiErrorMessage(err));
    }
  };

  return (
    <Card type="inner" title="就绪材料" size="small">
      {contextHolder}
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Space wrap>
          <Select
            style={{ minWidth: 200 }}
            value={selectedType}
            onChange={(v) => setSelectedType(v as ReadinessArtifactType)}
            options={ARTIFACT_TYPES.map((t) => ({
              label: ARTIFACT_TYPE_LABEL[t],
              value: t,
            }))}
          />
          <Popconfirm
            title={`生成 ${ARTIFACT_TYPE_LABEL[selectedType]}？`}
            description={
              latestByType.has(selectedType)
                ? "已有材料将被保留，新生成结果会出现在列表顶部。"
                : undefined
            }
            onConfirm={() => handleGenerate(selectedType)}
            disabled={resumeMissing || generating}
          >
            <Button
              type="primary"
              loading={generating}
              disabled={resumeMissing}
            >
              {latestByType.has(selectedType) ? "重新生成" : "生成"}
            </Button>
          </Popconfirm>
          {resumeMissing ? (
            <Text type="secondary">需先绑定简历版本</Text>
          ) : null}
        </Space>

        {generating && pollRunId ? (
          <Alert
            type="info"
            showIcon
            message={`${ARTIFACT_TYPE_LABEL[selectedType] ?? WORKFLOW_LABEL}生成中…`}
            description={
              <Space direction="vertical" size={0}>
                <Text type="secondary">{runIdHint(pollRunId)}</Text>
                <AgentRunStatusTag status="running" />
              </Space>
            }
          />
        ) : null}

        {loading && artifacts.length === 0 ? (
          <Spin />
        ) : artifacts.length === 0 ? (
          <Empty description="尚无材料，选择类型后点击生成。" />
        ) : (
          <List
            size="small"
            dataSource={ARTIFACT_TYPES.map((t) => ({
              type: t,
              artifact: latestByType.get(t) ?? null,
            }))}
            renderItem={({ type, artifact }) => (
              <List.Item>
                <ArtifactRow
                  type={type}
                  artifact={artifact}
                  generating={generating && selectedType === type}
                  onGenerate={() => handleGenerate(type)}
                  disabled={resumeMissing}
                />
              </List.Item>
            )}
          />
        )}

        <Text type="secondary">
          AI 生成内容为建议草稿，非最终定稿，请人工核对后再使用。
        </Text>
      </Space>
    </Card>
  );
}

/** A single artifact-type row: label, latest content preview, generate button. */
function ArtifactRow({
  type,
  artifact,
  generating,
  onGenerate,
  disabled,
}: {
  type: ReadinessArtifactType;
  artifact: ReadinessArtifactOut | null;
  generating: boolean;
  onGenerate: () => void;
  disabled: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <Space direction="vertical" size={4} style={{ width: "100%" }}>
      <Space>
        <Tag color={artifact ? "green" : "default"}>
          {artifact ? "已生成" : "未生成"}
        </Tag>
        <Text strong>{ARTIFACT_TYPE_LABEL[type]}</Text>
        {artifact ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {formatTime(artifact.created_at)}
          </Text>
        ) : null}
      </Space>
      {artifact ? (
        <Space direction="vertical" size={2} style={{ width: "100%" }}>
          <Paragraph
            style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 13 }}
            ellipsis={expanded ? false : { rows: 2, expandable: true, onExpand: () => setExpanded(true) }}
          >
            {formatContent(artifact.content)}
          </Paragraph>
          <Space>
            <Button size="small" onClick={() => setExpanded((v) => !v)}>
              {expanded ? "收起" : "展开"}
            </Button>
            <Popconfirm
              title={`重新生成 ${ARTIFACT_TYPE_LABEL[type]}？`}
              description="已有材料将被保留，新结果出现在列表顶部。"
              onConfirm={onGenerate}
              disabled={disabled || generating}
            >
              <Button size="small" loading={generating} disabled={disabled}>
                重新生成
              </Button>
            </Popconfirm>
          </Space>
        </Space>
      ) : (
        <Button
          size="small"
          type="primary"
          ghost
          loading={generating}
          disabled={disabled}
          onClick={onGenerate}
        >
          生成
        </Button>
      )}
    </Space>
  );
}

/** Parse the artifact content JSON into a readable preview string. */
function formatContent(content: string): string {
  try {
    const parsed = JSON.parse(content) as Record<string, unknown>;
    // Prefer human-readable fields common across the four output contracts.
    const candidates = ["message", "hook", "role_summary", "summary"];
    for (const key of candidates) {
      const v = parsed[key];
      if (typeof v === "string" && v.length > 0) return v;
    }
    // Fall back to the first string array field, joined.
    for (const [, v] of Object.entries(parsed)) {
      if (Array.isArray(v) && v.length > 0 && typeof v[0] === "string") {
        return (v as string[]).join("；");
      }
    }
    return JSON.stringify(parsed, null, 2);
  } catch {
    return content;
  }
}

function formatTime(ts: string | null): string {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}
