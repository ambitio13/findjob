import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  List,
  message,
  Select,
  Space,
  Spin,
  Tag,
  Timeline,
  Typography,
} from "antd";
import {
  apiErrorMessage,
  getAgentRunDetail,
  getJob,
  listAgentRuns,
  listJobAnalyses,
  listResumes,
  listResumeVersions,
  runJdAnalysis,
} from "@/api/client";
import {
  ACTIVE_AGENT_RUN_STATUSES,
  TERMINAL_AGENT_RUN_STATUSES,
  type AgentRunStatus,
} from "@/features/agent-runs/status";
import { useAgentRunPolling } from "@/features/agent-runs/useAgentRunPolling";
import {
  asyncRunFailureMessage,
  asyncRunSuccessMessage,
} from "@/features/agent-runs/copy";
import type {
  AgentRunDetailOut,
  AgentRunOut,
  AgentStepOut,
  JobOut,
  JobAnalysisDetailOut,
  ResumeOut,
  ResumeVersionListItem,
} from "@/types";

const { Paragraph, Text } = Typography;

const SEVERITY_COLOR: Record<string, string> = {
  low: "green",
  medium: "orange",
  high: "red",
};

const RECOMMENDATION_LABEL: Record<string, string> = {
  strong_match: "高度匹配",
  possible_match: "可能匹配",
  weak_match: "匹配度较低",
  not_enough_info: "信息不足",
};

const STEP_LABEL: Record<string, string> = {
  load_context: "加载上下文",
  build_prompt_context: "构建提示词",
  call_model: "调用模型",
  validate_model_output: "校验输出",
  persist_outputs: "持久化结果",
  complete_run: "完成运行",
};

const STEP_STATUS_COLOR: Record<string, string> = {
  succeeded: "green",
  failed: "red",
  running: "blue",
  skipped: "gray",
};

const RUN_STATUS_COLOR: Record<string, string> = {
  succeeded: "green",
  failed: "red",
  running: "blue",
  queued: "gray",
};

const WORKFLOW_LABEL = "分析";

/**
 * A run merged with its persisted analysis (if any). Successful runs have a
 * matching ``JobAnalysisDetailOut``; failed runs (which create no analysis row)
 * carry ``detail: null`` so they remain selectable and auditable.
 */
interface RunAnalysisView {
  run: AgentRunOut;
  detail: JobAnalysisDetailOut | null;
}

interface ResumeOption {
  resume: ResumeOut;
  versions: ResumeVersionListItem[];
}

/** Format an ISO timestamp (or null) for compact display. */
function formatTime(ts: string | null): string {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

/** Compute a human-readable duration between two ISO timestamps. */
function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return "-";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (Number.isNaN(ms) || ms < 0) return "-";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [resumes, setResumes] = useState<ResumeOut[]>([]);
  const [resumeOptions, setResumeOptions] = useState<ResumeOption[]>([]);
  const [resumesLoading, setResumesLoading] = useState(false);
  const [selectedResumeId, setSelectedResumeId] = useState<string | undefined>();
  const [selectedVersionId, setSelectedVersionId] = useState<
    string | undefined
  >();
  const [versionsLoading, setVersionsLoading] = useState(false);

  // Persisted analysis list, hydrated on load and refreshed after each run.
  const [analyses, setAnalyses] = useState<JobAnalysisDetailOut[]>([]);
  const [runs, setRuns] = useState<AgentRunOut[]>([]);
  const [analysesLoading, setAnalysesLoading] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  // The run currently being polled (null when no active run).
  const [pollRunId, setPollRunId] = useState<string | null>(null);
  const [messageApi, contextHolder] = message.useMessage();

  // R1/R2: hydrate persisted state for this job from BOTH the analyses list
  // (successful runs) and the agent-runs list filtered by job + workflow
  // (which also returns failed runs that create no JobAnalysis row). Merging
  // the two is what makes failed runs visible/auditable in the UI.
  const refreshData = useCallback(
    async (selectNewest = false) => {
      if (!id) return;
      setAnalysesLoading(true);
      try {
        const [analysisData, runData] = await Promise.all([
          listJobAnalyses(id, 1, 20),
          listAgentRuns(1, 20, id, "resume_aware_jd_analysis"),
        ]);
        setAnalyses(analysisData.items);
        setRuns(runData.items);
        if (selectNewest) {
          // listAgentRuns is newest-first by created_at desc; pick the most
          // recent run (this is how a freshly-failed run gets selected).
          setSelectedRunId(runData.items[0]?.id ?? null);
        } else {
          setSelectedRunId((prev) => prev ?? runData.items[0]?.id ?? null);
        }
      } catch (err) {
        messageApi.warning(apiErrorMessage(err));
      } finally {
        setAnalysesLoading(false);
      }
    },
    [id, messageApi],
  );

  // Shared polling hook — handles recursive setTimeout, token-based
  // cancellation, and cleanup on unmount. On each tick, refresh persisted
  // state so the merge of runs + analyses reflects the latest durable rows.
  useAgentRunPolling(pollRunId, {
    onUpdate: () => {
      void refreshData(false);
    },
    onTerminal: (detail) => {
      setRunning(false);
      setPollRunId(null);
      if (detail.status === "succeeded") {
        messageApi.success(asyncRunSuccessMessage(WORKFLOW_LABEL));
      } else {
        messageApi.warning(
          asyncRunFailureMessage(WORKFLOW_LABEL, detail.error),
        );
      }
    },
  });

  // Load job detail.
  useEffect(() => {
    if (!id) return;
    let active = true;
    (async () => {
      try {
        const data = await getJob(id);
        if (active) setJob(data);
      } catch (err) {
        if (active) setError(apiErrorMessage(err));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [id]);

  useEffect(() => {
    void refreshData();
  }, [refreshData]);

  // Merge runs (all runs for the job, including failed) with their persisted
  // analysis (if any). Runs are newest-first from the API.
  const runViews = useMemo<RunAnalysisView[]>(() => {
    const byRunId = new Map<string, JobAnalysisDetailOut>();
    for (const a of analyses) {
      const rid = a.analysis.agent_run_id;
      if (rid) byRunId.set(rid, a);
    }
    return runs.map((run) => ({ run, detail: byRunId.get(run.id) ?? null }));
  }, [runs, analyses]);

  // Load resumes for the current user.
  useEffect(() => {
    let active = true;
    (async () => {
      setResumesLoading(true);
      try {
        const data = await listResumes(1, 100);
        if (!active) return;
        setResumes(data.items);
      } catch (err) {
        if (active) messageApi.warning(apiErrorMessage(err));
      } finally {
        if (active) setResumesLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [messageApi]);

  // Load versions for all resumes once resumes arrive.
  useEffect(() => {
    let active = true;
    if (resumes.length === 0) {
      setResumeOptions([]);
      return;
    }
    (async () => {
      setVersionsLoading(true);
      try {
        const entries = await Promise.all(
          resumes.map(async (resume) => {
            let versions: ResumeVersionListItem[] = [];
            try {
              versions = await listResumeVersions(resume.id);
            } catch {
              versions = [];
            }
            return { resume, versions };
          }),
        );
        if (!active) return;
        setResumeOptions(entries);
        // Default-select the first resume's latest version.
        const first = entries[0];
        if (first && first.versions.length > 0) {
          setSelectedResumeId(first.resume.id);
          const latest = [...first.versions].sort(
            (a, b) => (b.version_no ?? 0) - (a.version_no ?? 0),
          )[0];
          setSelectedVersionId(latest.id);
        }
      } finally {
        if (active) setVersionsLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [resumes]);

  const versionsForSelected = useMemo(() => {
    const entry = resumeOptions.find((r) => r.resume.id === selectedResumeId);
    return entry?.versions ?? [];
  }, [resumeOptions, selectedResumeId]);

  // Duplicate active-run guard: while a queued/running run already exists for
  // this job the submit button stays disabled (the backend also enforces a 409
  // on duplicate submit, this is the UX mirror).
  const hasActiveRun = useMemo(
    () => runs.some((r) => ACTIVE_AGENT_RUN_STATUSES.has(r.status as AgentRunStatus)),
    [runs],
  );

  if (loading) return <Spin />;
  if (error || !job) {
    return <Alert type="error" message="加载失败" description={error ?? undefined} />;
  }

  const noResumes = resumes.length === 0 && !resumesLoading;

  const handleRun = async () => {
    if (!id || !selectedVersionId) return;
    setPollRunId(null);
    setRunning(true);
    try {
      const res = await runJdAnalysis(id, selectedVersionId);
      setSelectedRunId(res.run.id);
      // If the enqueue itself failed (Redis down), the backend flips the run
      // to ``failed`` before returning — surface that immediately.
      if (TERMINAL_AGENT_RUN_STATUSES.has(res.run.status as AgentRunStatus)) {
        setRunning(false);
        if (res.run.status === "succeeded") {
          messageApi.success(asyncRunSuccessMessage(WORKFLOW_LABEL));
        } else {
          messageApi.warning(
            asyncRunFailureMessage(WORKFLOW_LABEL, res.run.error),
          );
        }
        await refreshData(true);
        return;
      }
      // The run is ``queued`` — refresh once to reflect the queued row, then
      // poll until terminal status.
      await refreshData(false);
      setPollRunId(res.run.id);
    } catch (err) {
      setRunning(false);
      messageApi.error(apiErrorMessage(err));
      // Even on failure, the backend may persist a failed run (scoped to this
      // job via job_id) — refresh and force-select the newest run so the
      // failure trail is immediately visible (R4).
      await refreshData(true);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {contextHolder}
      <Card title="职位详情">
        <Descriptions column={2}>
          <Descriptions.Item label="平台">{job.platform}</Descriptions.Item>
          <Descriptions.Item label="公司">{job.company}</Descriptions.Item>
          <Descriptions.Item label="职位">{job.title}</Descriptions.Item>
          <Descriptions.Item label="城市">{job.location ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="薪资">{job.salary_range ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="方向">{job.direction ?? "-"}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="JD 原文">
        <Paragraph style={{ whiteSpace: "pre-wrap" }}>{job.jd_raw}</Paragraph>
      </Card>

      <JdNormalizedCard jdNormalized={job.jd_normalized} />

      <Card title="JD 分析">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {noResumes ? (
            <Alert
              type="info"
              showIcon
              message="尚未上传简历"
              description={
                <span>
                  请先前往{" "}
                  <Link to="/resumes">简历管理</Link> 上传简历后再运行分析。
                </span>
              }
            />
          ) : (
            <Space wrap>
              <Select
                style={{ minWidth: 240 }}
                placeholder="选择简历"
                loading={resumesLoading || versionsLoading}
                value={selectedResumeId}
                onChange={(value) => {
                  setSelectedResumeId(value);
                  const entry = resumeOptions.find(
                    (r) => r.resume.id === value,
                  );
                  if (entry && entry.versions.length > 0) {
                    const latest = [...entry.versions].sort(
                      (a, b) => (b.version_no ?? 0) - (a.version_no ?? 0),
                    )[0];
                    setSelectedVersionId(latest.id);
                  } else {
                    setSelectedVersionId(undefined);
                  }
                }}
                options={resumeOptions.map((r) => ({
                  label: r.resume.filename,
                  value: r.resume.id,
                }))}
              />
              <Select
                style={{ minWidth: 200 }}
                placeholder="选择简历版本"
                loading={versionsLoading}
                value={selectedVersionId}
                onChange={setSelectedVersionId}
                disabled={!selectedResumeId || versionsForSelected.length === 0}
                options={versionsForSelected.map((v) => ({
                  label: `v${v.version_no}`,
                  value: v.id,
                }))}
              />
              <Button
                type="primary"
                loading={running}
                disabled={!selectedVersionId || hasActiveRun}
                onClick={handleRun}
              >
                运行 JD 分析
              </Button>
            </Space>
          )}

          <AnalysisSection
            runViews={runViews}
            loading={analysesLoading}
            running={running}
            selectedRunId={selectedRunId}
            onSelectRun={setSelectedRunId}
          />
        </Space>
      </Card>
    </div>
  );
}

/**
 * R1/R2/R3/R4: explicit empty / running / success / failed states driven by
 * persisted rows rather than local POST response state. The view merges runs
 * (successful + failed) with their analyses; failed runs have no analysis and
 * are still selectable so their execution trail can be audited.
 */
function AnalysisSection({
  runViews,
  loading,
  running,
  selectedRunId,
  onSelectRun,
}: {
  runViews: RunAnalysisView[];
  loading: boolean;
  running: boolean;
  selectedRunId: string | null;
  onSelectRun: (id: string | null) => void;
}) {
  if (loading && runViews.length === 0) {
    return <Spin />;
  }

  if (running && runViews.length === 0) {
    return (
      <Alert
        type="info"
        showIcon
        message="分析运行中…"
        description="正在调用模型并持久化结果，请稍候。"
      />
    );
  }

  if (runViews.length === 0) {
    return <Empty description="暂未运行，选择简历版本后点击运行" />;
  }

  const selected =
    runViews.find((v) => v.run.id === selectedRunId) ?? runViews[0];

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Select
        style={{ minWidth: 360 }}
        value={selected.run.id}
        onChange={onSelectRun}
        options={runViews.map((v, idx) => {
          const isFailed = v.run.status === "failed" || !v.detail;
          const label = v.detail?.structured
            ? RECOMMENDATION_LABEL[v.detail.structured.recommendation] ??
              v.detail.structured.recommendation
            : isFailed
              ? "运行失败"
              : "运行中";
          return {
            label: `#${runViews.length - idx} · ${label} · ${formatTime(v.run.created_at)}`,
            value: v.run.id,
          };
        })}
      />
      {selected.detail ? (
        <AnalysisResult detail={selected.detail} />
      ) : (
        <Alert
          type="warning"
          showIcon
          message="该次运行未产出有效结果"
          description={
            <span>
              运行 ID: <Text code>{selected.run.id}</Text>。请在下方
              「Agent 执行轨迹」查看失败阶段。
            </span>
          }
        />
      )}
      <AgentProcessPanel runId={selected.run.id} />
    </Space>
  );
}

function AnalysisResult({ detail }: { detail: JobAnalysisDetailOut }) {
  const { analysis, artifact, structured } = detail;

  // Failed / partial runs have no structured output to render.
  if (!structured) {
    return (
      <Alert
        type="warning"
        showIcon
        message="该次运行未产出有效结果"
        description={
          <span>
            运行 ID: <Text code>{analysis.agent_run_id ?? "-"}</Text>。请在下方
            「Agent 执行轨迹」查看失败阶段。
          </span>
        }
      />
    );
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Descriptions column={2} bordered size="small">
        <Descriptions.Item label="Run ID">
          {analysis.agent_run_id ?? "-"}
        </Descriptions.Item>
        <Descriptions.Item label="匹配分">
          {structured.match_score ?? "-"}
        </Descriptions.Item>
        <Descriptions.Item label="风险分">
          {structured.risk_score ?? "-"}
        </Descriptions.Item>
        <Descriptions.Item label="推荐结论" span={2}>
          <Tag>
            {RECOMMENDATION_LABEL[structured.recommendation] ??
              structured.recommendation}
          </Tag>
        </Descriptions.Item>
      </Descriptions>

      <Card type="inner" title="岗位概述" size="small">
        <Paragraph>{structured.role_summary}</Paragraph>
        {structured.responsibilities.length > 0 && (
          <>
            <Text strong>职责</Text>
            <List
              size="small"
              dataSource={structured.responsibilities}
              renderItem={(item) => <List.Item>{item}</List.Item>}
            />
          </>
        )}
      </Card>

      <Card type="inner" title="要求" size="small">
        <Text strong>硬性要求</Text>
        <List
          size="small"
          dataSource={structured.hard_requirements}
          renderItem={(item) => <List.Item>{item}</List.Item>}
        />
        <Text strong>加分项</Text>
        <List
          size="small"
          dataSource={structured.nice_to_have_requirements}
          renderItem={(item) => <List.Item>{item}</List.Item>}
        />
      </Card>

      <Card type="inner" title="风险点" size="small">
        <List
          size="small"
          dataSource={structured.risk_points}
          renderItem={(item) => (
            <List.Item>
              <Space direction="vertical" size={0}>
                <Space>
                  <Tag color={SEVERITY_COLOR[item.severity] ?? "default"}>
                    {item.severity}
                  </Tag>
                  <Text strong>{item.title}</Text>
                </Space>
                <Text type="secondary">{item.detail}</Text>
              </Space>
            </List.Item>
          )}
        />
      </Card>

      {structured.skill_gaps.length > 0 && (
        <Card type="inner" title="技能差距" size="small">
          <List
            size="small"
            dataSource={structured.skill_gaps}
            renderItem={(item) => <List.Item>{item}</List.Item>}
          />
        </Card>
      )}

      {structured.interview_preparation.length > 0 && (
        <Card type="inner" title="面试准备" size="small">
          <List
            size="small"
            dataSource={structured.interview_preparation}
            renderItem={(item) => <List.Item>{item}</List.Item>}
          />
        </Card>
      )}

      <Card type="inner" title="薪酬 / 成长 / 稳定性" size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="薪酬">{structured.salary_note}</Descriptions.Item>
          <Descriptions.Item label="成长">{structured.growth_note}</Descriptions.Item>
          <Descriptions.Item label="稳定性">
            {structured.stability_note}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {artifact && (
        <Card type="inner" title="来源元数据" size="small">
          <Descriptions column={1} size="small">
            <Descriptions.Item label="分析 ID">{analysis.id}</Descriptions.Item>
            <Descriptions.Item label="产物 ID">{artifact.id}</Descriptions.Item>
            <Descriptions.Item label="产物类型">
              {artifact.artifact_type}
            </Descriptions.Item>
            <Descriptions.Item label="Prompt 版本">
              {artifact.prompt_version ?? "-"}
            </Descriptions.Item>
            <Descriptions.Item label="模型">
              {artifact.model_name ?? "-"}
            </Descriptions.Item>
            {artifact.source_ids?.provider ? (
              <Descriptions.Item label="Provider">
                {String(artifact.source_ids.provider)}
              </Descriptions.Item>
            ) : null}
            {artifact.source_ids?.resume_version_id ? (
              <Descriptions.Item label="简历版本">
                {String(artifact.source_ids.resume_version_id)}
              </Descriptions.Item>
            ) : null}
          </Descriptions>
          <Text type="secondary">
            以上分析为模型生成的草稿，非已完成的定制简历。
          </Text>
        </Card>
      )}
    </Space>
  );
}

/**
 * R3: fetches and displays the ordered sanitized step trail for the selected
 * run. Failed steps surface their sanitized error; the panel makes the audit
 * trail visible without exposing raw prompts or resume text. Each step also
 * renders its sanitized ``result`` metadata (provider/model/prompt version,
 * counts, timing, error type) and ``created_at`` timestamp.
 */
function AgentProcessPanel({ runId }: { runId: string | null }) {
  const [detail, setDetail] = useState<AgentRunDetailOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) {
      setDetail(null);
      return;
    }
    let active = true;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await getAgentRunDetail(runId);
        if (active) setDetail(data);
      } catch (err) {
        if (active) setError(apiErrorMessage(err));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [runId]);

  if (!runId) return null;
  if (loading) return <Spin />;
  if (error || !detail) {
    return (
      <Card type="inner" title="Agent 执行轨迹" size="small">
        <Alert type="error" message="轨迹加载失败" description={error ?? undefined} />
      </Card>
    );
  }

  const totalDuration = formatDuration(detail.started_at, detail.finished_at);

  return (
    <Card type="inner" title="Agent 执行轨迹" size="small">
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Descriptions column={3} size="small">
          <Descriptions.Item label="运行状态">
            <Tag color={RUN_STATUS_COLOR[detail.status] ?? "default"}>
              {detail.status}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="开始">
            {formatTime(detail.started_at)}
          </Descriptions.Item>
          <Descriptions.Item label="耗时">
            {detail.status === "running" ? "进行中" : totalDuration}
          </Descriptions.Item>
        </Descriptions>
        {detail.error ? (
          <Text type="danger">错误: {detail.error}</Text>
        ) : null}
        <Timeline
          items={detail.steps.map((step: AgentStepOut) => ({
            color: STEP_STATUS_COLOR[step.status] ?? "gray",
            children: (
              <Space direction="vertical" size={0} style={{ width: "100%" }}>
                <Space>
                  <Text strong>
                    {step.step_no}. {STEP_LABEL[step.name] ?? step.name}
                  </Text>
                  <Tag color={STEP_STATUS_COLOR[step.status] ?? "default"}>
                    {step.status}
                  </Tag>
                  <Text type="secondary">{formatTime(step.created_at)}</Text>
                </Space>
                {step.error ? (
                  <Text type="danger">{step.error}</Text>
                ) : null}
                <StepMetadata step={step} />
              </Space>
            ),
          }))}
        />
        <Text type="secondary">
          轨迹仅记录阶段与脱敏后的元数据（provider/model/耗时/prompt 版本等），不包含原始提示词或简历内容。
        </Text>
      </Space>
    </Card>
  );
}

/**
 * Renders a step's sanitized ``result`` metadata as a compact key→value list.
 * Special-cases ``latency_ms`` (call_model step) for emphasis. Filters out
 * null/undefined values to keep the list tight.
 */
function StepMetadata({ step }: { step: AgentStepOut }) {
  if (!step.result) return null;
  const entries = Object.entries(step.result).filter(
    ([, v]) => v !== null && v !== undefined,
  );
  if (entries.length === 0) return null;

  return (
    <Descriptions column={1} size="small" bordered style={{ marginTop: 4 }}>
      {entries.map(([key, value]) => (
        <Descriptions.Item
          key={key}
          label={
            key === "latency_ms" ? (
              <Text strong>模型耗时</Text>
            ) : (
              <Text type="secondary">{key}</Text>
            )
          }
        >
          {key === "latency_ms" && typeof value === "number" ? (
            <Tag color="blue">{value}ms</Tag>
          ) : (
            <Text code>{formatMetaValue(value)}</Text>
          )}
        </Descriptions.Item>
      ))}
    </Descriptions>
  );
}

/** Stringify a metadata value for display (objects/arrays compactly). */
function formatMetaValue(value: unknown): string {
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/**
 * Renders the structured parse draft persisted in ``jd_normalized`` when
 * present. The durable contract (design.md §"jd_normalized shape") stores the
 * model-parsed draft under ``fields`` and parse provenance under
 * ``_extraction``; the job's own columns hold the user-edited final values.
 */
function JdNormalizedCard({
  jdNormalized,
}: {
  jdNormalized: Record<string, unknown> | null;
}) {
  if (!jdNormalized) return null;

  const fieldsBlob = jdNormalized.fields;
  const draftFields: Partial<{
    responsibilities: string[];
    hard_requirements: string[];
    nice_to_have_requirements: string[];
    benefits_or_risk_clues: string[];
    uncertain_fields: { field: string; reason: string | null }[];
  }> =
    typeof fieldsBlob === "object" && fieldsBlob !== null
      ? (fieldsBlob as Record<string, unknown>)
      : {};

  const responsibilities = asStringArray(draftFields.responsibilities);
  const hardRequirements = asStringArray(draftFields.hard_requirements);
  const niceToHave = asStringArray(draftFields.nice_to_have_requirements);
  const benefitsOrRisk = asStringArray(draftFields.benefits_or_risk_clues);
  const uncertainFields = asUncertainFields(draftFields.uncertain_fields);
  const extraction = asExtractionInfo(jdNormalized._extraction);

  const hasLists =
    responsibilities.length > 0 ||
    hardRequirements.length > 0 ||
    niceToHave.length > 0 ||
    benefitsOrRisk.length > 0;

  if (!hasLists && uncertainFields.length === 0 && !extraction) return null;

  return (
    <Card title="解析草稿（jd_normalized）" size="small">
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        {extraction && (
          <Descriptions column={2} size="small">
            {extraction.status && (
              <Descriptions.Item label="解析状态">
                {extraction.status}
              </Descriptions.Item>
            )}
            {extraction.prompt_version && (
              <Descriptions.Item label="Prompt 版本">
                {extraction.prompt_version}
              </Descriptions.Item>
            )}
            {extraction.provider && (
              <Descriptions.Item label="Provider">
                {extraction.provider}
              </Descriptions.Item>
            )}
            {extraction.model && (
              <Descriptions.Item label="模型">
                {extraction.model}
              </Descriptions.Item>
            )}
            {extraction.run_id && (
              <Descriptions.Item label="运行 ID" span={2}>
                <Text code>{extraction.run_id}</Text>
              </Descriptions.Item>
            )}
            {extraction.parsed_at && (
              <Descriptions.Item label="解析时间" span={2}>
                {formatTime(extraction.parsed_at)}
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
        {responsibilities.length > 0 && (
          <StringListCard title="职责" items={responsibilities} />
        )}
        {hardRequirements.length > 0 && (
          <StringListCard title="硬性要求" items={hardRequirements} />
        )}
        {niceToHave.length > 0 && (
          <StringListCard title="加分项" items={niceToHave} />
        )}
        {benefitsOrRisk.length > 0 && (
          <StringListCard title="福利/风险线索" items={benefitsOrRisk} />
        )}
        {uncertainFields.length > 0 && (
          <Card type="inner" title="不确定字段" size="small">
            <List
              size="small"
              dataSource={uncertainFields}
              renderItem={(item) => (
                <List.Item>
                  <Space direction="vertical" size={0}>
                    <Text strong>{item.field}</Text>
                    {item.reason ? (
                      <Text type="secondary">{item.reason}</Text>
                    ) : null}
                  </Space>
                </List.Item>
              )}
            />
          </Card>
        )}
        <Text type="secondary">
          以上为解析时的草稿快照，顶部字段为最终保存值。
        </Text>
      </Space>
    </Card>
  );
}

function StringListCard({ title, items }: { title: string; items: string[] }) {
  return (
    <Card type="inner" title={title} size="small">
      <List
        size="small"
        dataSource={items}
        renderItem={(item) => <List.Item>{item}</List.Item>}
      />
    </Card>
  );
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === "string");
}

function asUncertainFields(
  value: unknown,
): { field: string; reason: string | null }[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((v): v is Record<string, unknown> => typeof v === "object" && v !== null)
    .map((v) => ({
      field: typeof v.field === "string" ? v.field : String(v.field ?? ""),
      reason:
        typeof v.reason === "string" || v.reason == null
          ? (v.reason as string | null)
          : String(v.reason),
    }))
    .filter((v) => v.field);
}

function asExtractionInfo(
  value: unknown,
): {
  status?: string;
  run_id?: string;
  parsed_at?: string;
  prompt_version?: string;
  provider?: string;
  model?: string;
} | null {
  if (typeof value !== "object" || value === null) return null;
  const v = value as Record<string, unknown>;
  return {
    status: typeof v.status === "string" ? v.status : undefined,
    run_id: typeof v.run_id === "string" ? v.run_id : undefined,
    parsed_at: typeof v.parsed_at === "string" ? v.parsed_at : undefined,
    prompt_version:
      typeof v.prompt_version === "string" ? v.prompt_version : undefined,
    provider: typeof v.provider === "string" ? v.provider : undefined,
    model: typeof v.model === "string" ? v.model : undefined,
  };
}
