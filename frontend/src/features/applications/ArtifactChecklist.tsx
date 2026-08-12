import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  List,
  message,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import axios from "axios";
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
  "targeted_resume",
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
  /**
   * When ``true``, auto-generate all four readiness artifacts on mount. The
   * parent sets this *only* for genuinely new records (opened directly from a
   * successful create-application flow), never for records loaded via a later
   * GET — that would risk spuriously auto-generating on every visit to an old
   * planned record. When ``false``/``undefined`` auto-generation never fires,
   * so old records are safe regardless of their status/artifacts.
   */
  autoGenerate?: boolean;
}

/**
 * Checklist + generator for the readiness artifact types.
 *
 * Supports both single-type generation (user clicks one) and bulk
 * auto-generation (all types triggered in parallel on mount when
 * ``autoGenerate`` is ``true``). The 409 duplicate-active-run guard is silently
 * swallowed during auto-generation so a re-mount never blocks on an in-flight
 * run. Note ``targeted_resume`` fails fast (non-retryable) when the resume
 * has no extracted facts — the backend refuses untraceable rewrites.
 *
 * Multiple concurrent runs are tracked via a ``Map<type, runId>``. A single
 * ``useAgentRunPolling`` instance polls the most-recently-enqueued run; on
 * each tick the artifacts list is refreshed so completed types appear
 * immediately while others continue.
 */
export function ArtifactChecklist({
  application,
  onTerminal,
  resumeMissing,
  onStateChange,
  autoGenerate,
}: Props) {
  const [artifacts, setArtifacts] = useState<ReadinessArtifactOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedType, setSelectedType] = useState<ReadinessArtifactType>(
    "hr_opening_message",
  );
  // Track all active generation runs: type → runId. When non-empty, the panel
  // shows an aggregate "generating" state.
  const [pollRunIds, setPollRunIds] = useState<
    Map<ReadinessArtifactType, string>
  >(new Map());
  const [messageApi, contextHolder] = message.useMessage();

  // The polling hook only accepts a single runId at a time. We poll the first
  // active run; on each tick we refresh artifacts (which surfaces completed
  // types) and advance to the next active run. This is simpler than spawning
  // N polling hooks and still gives near-real-time updates.
  const activeRunId = useMemo(() => {
    const ids = Array.from(pollRunIds.values());
    return ids.length > 0 ? ids[0] : null;
  }, [pollRunIds]);

  const generating = pollRunIds.size > 0;

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

  /** Remove a run from the active set (called on terminal). */
  const clearRun = useCallback((runId: string) => {
    setPollRunIds((prev) => {
      const next = new Map(prev);
      for (const [k, v] of next) {
        if (v === runId) next.delete(k);
      }
      return next;
    });
  }, []);

  // Poll the currently-active run. On terminal, remove it from the active set,
  // refresh artifacts, and notify the parent. If other runs are still active,
  // the hook will re-arm on the next render (activeRunId changes).
  useAgentRunPolling(activeRunId, {
    onUpdate: () => {
      // Refresh on each tick so completed types appear while others continue.
      void refresh();
    },
    onTerminal: (detail) => {
      if (detail.id) clearRun(detail.id);
      if (detail.status === "succeeded") {
        messageApi.success("材料生成完成");
      } else {
        messageApi.warning(asyncRunFailureMessage(WORKFLOW_LABEL, detail.error));
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

  /** Enqueue a single artifact generation run. Returns the runId or null. */
  const enqueueGeneration = useCallback(
    async (
      type: ReadinessArtifactType,
      opts?: { silentOn409?: boolean },
    ): Promise<string | null> => {
      if (resumeMissing) {
        messageApi.error("尚未绑定简历版本，无法生成材料");
        return null;
      }
      try {
        const res = await generateReadinessArtifact(application.id, type);
        // If enqueue itself failed (Redis down), the backend flips to failed.
        if (TERMINAL_AGENT_RUN_STATUSES.has(res.run.status as AgentRunStatus)) {
          if (res.run.status === "succeeded") {
            messageApi.success(`${ARTIFACT_TYPE_LABEL[type]}生成完成`);
          } else {
            messageApi.warning(
              asyncRunFailureMessage(ARTIFACT_TYPE_LABEL[type], res.run.error),
            );
          }
          void refresh();
          onTerminal();
          return null;
        }
        return res.run.id;
      } catch (err) {
        // 409 = duplicate active run: during auto-generation this is expected
        // (re-mount, or a run already in flight). Swallow silently when
        // silentOn409 is set; otherwise surface a friendly message.
        if (opts?.silentOn409 && axios.isAxiosError(err) && err.response?.status === 409) {
          return null;
        }
        messageApi.error(apiErrorMessage(err));
        return null;
      }
    },
    [application.id, resumeMissing, refresh, onTerminal, messageApi],
  );

  /**
   * Enqueue generation for all artifact types in order, collecting their
   * runIds into ``pollRunIds`` (which re-arms the shared polling hook). The
   * ``silent`` flag controls the "正在自动生成就绪材料…" toast — auto
   * generation on mount shows it, while the manual "一键生成全部" button does
   * not (the Popconfirm already signals intent).
   */
  const generateAll = useCallback(
    async (silent: boolean): Promise<void> => {
      if (silent) messageApi.info("正在自动生成就绪材料…");
      const entries: [ReadinessArtifactType, string][] = [];
      for (const type of ARTIFACT_TYPES) {
        const runId = await enqueueGeneration(type, { silentOn409: true });
        if (runId) entries.push([type, runId]);
      }
      if (entries.length > 0) {
        setPollRunIds((prev) => {
          const next = new Map(prev);
          for (const [type, runId] of entries) next.set(type, runId);
          return next;
        });
      }
    },
    [enqueueGeneration, messageApi],
  );

  const handleGenerate = async (type: ReadinessArtifactType) => {
    setSelectedType(type);
    const runId = await enqueueGeneration(type);
    if (runId) {
      messageApi.info(asyncRunProgressMessage(ARTIFACT_TYPE_LABEL[type] ?? WORKFLOW_LABEL));
      setPollRunIds((prev) => {
        const next = new Map(prev);
        next.set(type, runId);
        return next;
      });
    }
  };

  // ── Auto-generation ──────────────────────────────────────────────────────
  // On first mount, auto-generate all four readiness artifacts *only* when the
  // parent explicitly signals a fresh create (``autoGenerate === true``). We do
  // NOT infer freshness from ``application.status``/``artifacts.length``/
  //``application.is_duplicate`` because those are unreliable after a navigation
  // that re-GETs the record (``is_duplicate`` defaults to false, so an old
  // planned record with no artifacts would spuriously trigger). A ref guard
  // ensures this only fires once per mount.
  const autoGenFiredRef = useRef(false);
  useEffect(() => {
    if (autoGenFiredRef.current) return;
    if (!autoGenerate) return;
    if (resumeMissing) return;
    if (pollRunIds.size > 0) return;

    autoGenFiredRef.current = true;
    void generateAll(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoGenerate, resumeMissing, pollRunIds.size]);

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
          <Popconfirm
            title="一键生成全部材料？"
            description="将按顺序串行生成全部五类就绪材料，已有材料会被保留。"
            onConfirm={() => void generateAll(false)}
            disabled={resumeMissing || generating}
          >
            <Button
              loading={generating}
              disabled={resumeMissing || generating}
            >
              一键生成全部
            </Button>
          </Popconfirm>
        </Space>

        {generating ? (
          <Alert
            type="info"
            showIcon
            message="材料生成中…"
            description={
              <Space direction="vertical" size={0}>
                {Array.from(pollRunIds.entries()).map(([type, runId]) => (
                  <Space key={type} size="small">
                    <Text type="secondary">{ARTIFACT_TYPE_LABEL[type]}</Text>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {runIdHint(runId)}
                    </Text>
                    <AgentRunStatusTag status="running" />
                  </Space>
                ))}
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
                  generating={generating && pollRunIds.has(type)}
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
          {type === "targeted_resume" ? (
            <TargetedResumePreview artifact={artifact} />
          ) : (
            <Paragraph
              style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 13 }}
              ellipsis={expanded ? false : { rows: 2, expandable: true, onExpand: () => setExpanded(true) }}
            >
              {formatContent(artifact.content)}
            </Paragraph>
          )}
          <Space>
            {type === "targeted_resume" ? null : (
              <Button size="small" onClick={() => setExpanded((v) => !v)}>
                {expanded ? "收起" : "展开"}
              </Button>
            )}
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
    // Prefer human-readable fields common across the output contracts.
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

// ---------------------------------------------------------------------------
// targeted_resume preview: traceable bullets + editable one-page markdown
// ---------------------------------------------------------------------------

interface TargetedResumeBulletView {
  section: string;
  bullet: string;
  matched_requirement: string;
  source_fact_refs: string[];
}

interface TargetedResumeView {
  headline: string;
  targeted_bullets: TargetedResumeBulletView[];
  matched_requirements: string[];
  do_not_claim: string[];
  one_page_markdown: string;
}

function parseTargetedResume(content: string): TargetedResumeView | null {
  try {
    const parsed = JSON.parse(content) as TargetedResumeView;
    if (!parsed || !Array.isArray(parsed.targeted_bullets)) return null;
    return parsed;
  } catch {
    return null;
  }
}

/**
 * Render a one-page resume image from markdown via an offscreen canvas.
 * Deliberately dependency-free: headings are bold, bullets get a marker,
 * and the PNG is downloaded through a temporary object URL.
 */
function downloadResumeImage(markdown: string, filename: string) {
  const lines = markdown.split("\n");
  const lineHeight = 24;
  const padding = 36;
  const width = 860;
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = lines.length * lineHeight + padding * 2;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#1f1f1f";
  ctx.textBaseline = "top";
  lines.forEach((rawLine, i) => {
    const y = padding + i * lineHeight;
    const isHeading = /^#{1,3}\s/.test(rawLine);
    ctx.font = isHeading ? "bold 17px sans-serif" : "14px sans-serif";
    const text = rawLine
      .replace(/^#{1,3}\s+/, "")
      .replace(/^[-*]\s+/, "• ");
    ctx.fillText(text, padding, y, width - padding * 2);
  });
  canvas.toBlob((blob) => {
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }, "image/png");
}

/**
 * Dedicated preview for ``targeted_resume``: shows the headline, every
 * rewritten bullet with its JD match + traceable fact refs, the
 * ``do_not_claim`` guard list, and copy/download/edit actions for the
 * one-page markdown.
 */
function TargetedResumePreview({ artifact }: { artifact: ReadinessArtifactOut }) {
  const view = useMemo(() => parseTargetedResume(artifact.content), [artifact.content]);
  const [editOpen, setEditOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [applied, setApplied] = useState<string | null>(null);
  const [messageApi, contextHolder] = message.useMessage();

  if (!view) {
    return (
      <Paragraph style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 13 }}>
        {artifact.content}
      </Paragraph>
    );
  }

  const markdown = applied ?? view.one_page_markdown ?? "";

  const copyMarkdown = async () => {
    try {
      await navigator.clipboard.writeText(markdown);
      messageApi.success("已复制一页式简历 Markdown");
    } catch {
      messageApi.error("复制失败，请手动选择文本复制");
    }
  };

  return (
    <Space direction="vertical" size={4} style={{ width: "100%" }}>
      {contextHolder}
      <Text strong style={{ fontSize: 13 }}>
        {view.headline}
      </Text>
      <List
        size="small"
        dataSource={view.targeted_bullets}
        renderItem={(b) => (
          <List.Item style={{ padding: "4px 0" }}>
            <Space direction="vertical" size={0} style={{ width: "100%" }}>
              <Text style={{ fontSize: 13 }}>
                【{b.section}】{b.bullet}
              </Text>
              <Space size={4} wrap>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  匹配：{b.matched_requirement}
                </Text>
                {(b.source_fact_refs ?? []).map((ref) => (
                  <Tag key={ref} color="blue" style={{ fontSize: 11 }}>
                    溯源 {ref}
                  </Tag>
                ))}
              </Space>
            </Space>
          </List.Item>
        )}
      />
      {view.do_not_claim && view.do_not_claim.length > 0 ? (
        <Text type="danger" style={{ fontSize: 12 }}>
          不可声称：{view.do_not_claim.join("；")}
        </Text>
      ) : null}
      <Space wrap>
        <Button size="small" onClick={() => { setDraft(markdown); setEditOpen(true); }}>
          编辑
        </Button>
        <Button size="small" type="primary" ghost onClick={() => void copyMarkdown()}>
          复制一页简历
        </Button>
        <Button
          size="small"
          onClick={() => downloadResumeImage(markdown, "targeted-resume.png")}
        >
          下载图片
        </Button>
        {applied !== null ? (
          <Button size="small" onClick={() => setApplied(null)}>
            恢复生成版
          </Button>
        ) : null}
      </Space>
      <Modal
        title="编辑一页式简历（仅本地修改，不影响已生成材料）"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={() => {
          setApplied(draft);
          setEditOpen(false);
        }}
        okText="应用"
        cancelText="取消"
        width={720}
      >
        <Input.TextArea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          autoSize={{ minRows: 12, maxRows: 24 }}
        />
      </Modal>
    </Space>
  );
}

function formatTime(ts: string | null): string {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}
