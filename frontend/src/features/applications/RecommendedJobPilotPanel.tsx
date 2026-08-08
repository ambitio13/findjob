import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Input,
  Popconfirm,
  Space,
  Steps,
  Switch,
  Tag,
  Typography,
  message,
} from "antd";
import {
  apiErrorMessage,
  approveApplicationAction,
  executeCommunicate,
  inspectCurrentJob,
  matchJob,
  prepareCommunicate,
} from "@/api/client";
import { useBridgeStatus } from "./useBridgeStatus";
import type {
  ApplicationActionOut,
  ApplicationOut,
  BridgeStatusResponse,
  CommunicateExecuteOut,
  CommunicatePrepareOut,
  InspectJobOut,
  JobOut,
  MatchDecisionOut,
} from "@/types";

const { Text, Paragraph } = Typography;

/** Terminal status labels for the communicate execute result. */
const EXECUTE_RESULT_LABEL: Record<string, string> = {
  submitted: "已发送",
  duplicate: "重复沟通",
  unknown: "结果未知",
  failed: "发送失败",
};

const EXECUTE_RESULT_COLOR: Record<string, string> = {
  submitted: "green",
  duplicate: "gold",
  unknown: "volcano",
  failed: "red",
};

/** Decision labels for the match step. */
const DECISION_LABEL: Record<string, string> = {
  communicate: "建议沟通",
  skip: "跳过",
  needs_review: "需人工审阅",
};

const DECISION_COLOR: Record<string, string> = {
  communicate: "green",
  skip: "default",
  needs_review: "orange",
};

/** Inspect status labels. */
const INSPECT_LABEL: Record<string, string> = {
  ok: "成功",
  jd_too_sparse: "JD 信息不足",
  read_failed: "读取失败",
};

/**
 * Which step the pilot flow is currently on. The Steps component uses 0-based
 * indices; we use a named enum for readability in the state logic.
 */
type PilotStep = "inspect" | "match" | "prepare" | "approve" | "execute" | "done";

const STEP_ORDER: PilotStep[] = [
  "inspect",
  "match",
  "prepare",
  "approve",
  "execute",
  "done",
];

function stepIndex(step: PilotStep): number {
  return STEP_ORDER.indexOf(step);
}

interface Props {
  application: ApplicationOut;
  onAfterChange: () => void;
}

/**
 * RecommendedJobPilotPanel — the GUI for the BOSS immediate-communicate flow.
 *
 * Orchestrates the five-step pipeline: inspect → match → prepare → approve →
 * execute. Each step calls a single-job backend endpoint (no batch paths).
 *
 * Two modes:
 *   - **Manual (default)**: each step requires a button click.
 *   - **Semi-auto loop**: a top-level switch. When on, steps 1→2→3 (inspect,
 *     match, prepare) run automatically in sequence. The flow **always stops**
 *     at approve and execute — these require human action. This does not
 *     violate the "no batch paths" or "execute needs approval" safety
 *     invariants: the loop only automates read-only / side-effect-free steps
 *     and the backend still enforces single-job-per-call.
 */
export function RecommendedJobPilotPanel({ application, onAfterChange }: Props) {
  // --- flow state -------------------------------------------------------
  const [step, setStep] = useState<PilotStep>("inspect");
  const [semiAuto, setSemiAuto] = useState(false);
  const [busy, setBusy] = useState(false);

  // step results
  const [inspectResult, setInspectResult] = useState<InspectJobOut | null>(null);
  const [matchResult, setMatchResult] = useState<MatchDecisionOut | null>(null);
  const [prepareResult, setPrepareResult] =
    useState<CommunicatePrepareOut | null>(null);
  const [executeResult, setExecuteResult] =
    useState<CommunicateExecuteOut | null>(null);

  // editable opening message (user can tweak before prepare)
  const [openingMessage, setOpeningMessage] = useState("");

  // error from the last step (cleared on next action)
  const [error, setError] = useState<string | null>(null);

  // agent_run_id from the last step result, surfaced in the error UI so the
  // user can locate the run in logs / agent-run detail without exposing HR
  // message content or other sensitive payloads.
  const [lastAgentRunId, setLastAgentRunId] = useState<string | null>(null);

  const [messageApi, contextHolder] = message.useMessage();

  // Resume version is required for match/prepare. Fall back to the
  // application's resume_version_id, but allow the user to override via
  // inspect (the inspect endpoint can accept a resume_version_id to create
  // the application link).
  const resumeVersionId = application.resume_version_id;

  // Bridge status — polls every 5s. The entire flow needs the userscript
  // connected (inspect reads JD via the bridge; execute clicks + sends via
  // the bridge).
  const { status: bridgeStatus } = useBridgeStatus(true);
  const bridgeConnected = bridgeStatus?.connected ?? false;

  // Track the job_id from inspect — match/prepare/execute all need it.
  const jobId = useMemo(() => {
    if (inspectResult?.job?.id) return inspectResult.job.id;
    return application.job_id;
  }, [inspectResult, application.job_id]);

  // Track the match artifact_id — prepare needs it.
  const matchArtifactId = matchResult?.artifact_id ?? null;

  // Track the action from prepare — approve/execute need it.
  const action = (prepareResult?.action ?? executeResult?.action ?? null) as
    | ApplicationActionOut
    | null;
  const actionId = action?.id ?? null;
  const applicationId = action?.application_id ?? application.id;

  // Reset everything when the application record changes (user switched).
  useEffect(() => {
    setStep("inspect");
    setInspectResult(null);
    setMatchResult(null);
    setPrepareResult(null);
    setExecuteResult(null);
    setOpeningMessage("");
    setError(null);
    setLastAgentRunId(null);
  }, [application.id]);

  // --- semi-auto loop driver -------------------------------------------
  //
  // When semiAuto is on and we arrive at a step that can be auto-run
  // (inspect, match, prepare), trigger it automatically. We use a ref to
  // avoid double-firing in StrictMode.
  const autoFiredRef = useRef<string>("");
  useEffect(() => {
    if (!semiAuto) return;
    if (busy) return;

    // Only auto-run steps that are side-effect-free.
    // inspect/match/prepare are safe. approve/execute always need human.
    const canAutoRun = step === "inspect" || step === "match" || step === "prepare";
    if (!canAutoRun) return;

    // Dedup: don't re-fire for the same step+application+busy cycle.
    const fireKey = `${application.id}:${step}`;
    if (autoFiredRef.current === fireKey) return;
    autoFiredRef.current = fireKey;

    // Fire the appropriate action.
    if (step === "inspect") {
      void handleInspect();
    } else if (step === "match") {
      void handleMatch();
    } else if (step === "prepare") {
      void handlePrepare();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [semiAuto, step, busy, application.id]);

  // Clear the dedup ref when step changes so a re-entry into the same step
  // (after a manual reset) can fire again.
  useEffect(() => {
    autoFiredRef.current = "";
  }, [step]);

  // --- step handlers ----------------------------------------------------

  const handleInspect = useCallback(async () => {
    setBusy(true);
    setError(null);
    setLastAgentRunId(null);
    try {
      const result = await inspectCurrentJob(resumeVersionId);
      setInspectResult(result);
      if (result.agent_run_id) setLastAgentRunId(result.agent_run_id);
      if (result.inspect_status === "ok" && result.job) {
        setStep("match");
        messageApi.success("职位读取成功");
      } else {
        setStep("inspect"); // stay on inspect
        setError(result.message ?? "读取失败，请确认 BOSS 页面已打开职位详情");
        if (semiAuto) setSemiAuto(false); // stop the loop on failure
      }
    } catch (err) {
      setError(apiErrorMessage(err));
      if (semiAuto) setSemiAuto(false);
    } finally {
      setBusy(false);
    }
  }, [resumeVersionId, messageApi, semiAuto]);

  const handleMatch = useCallback(async () => {
    if (!jobId) {
      setError("缺少职位 ID，请先读取职位");
      return;
    }
    if (!resumeVersionId) {
      setError("缺少简历版本 ID");
      return;
    }
    setBusy(true);
    setError(null);
    setLastAgentRunId(null);
    try {
      const result = await matchJob(jobId, resumeVersionId);
      setMatchResult(result);
      if (result.agent_run_id) setLastAgentRunId(result.agent_run_id);
      if (result.opening_message) {
        setOpeningMessage(result.opening_message);
      }
      if (result.decision === "communicate") {
        setStep("prepare");
        messageApi.success("匹配通过，建议沟通");
      } else if (result.decision === "needs_review") {
        // needs_review: advance to the prepare step so the user can review
        // risks/missing requirements and manually decide whether to prepare,
        // but stop the semi-auto loop — prepare must be a conscious human
        // action when the match is not a clear "communicate".
        setStep("prepare");
        if (semiAuto) setSemiAuto(false);
        messageApi.info("匹配结果为「需人工审阅」，请查看风险与缺失项后决定是否继续");
      } else {
        // skip — hard stop, do not proceed.
        setStep("match");
        if (semiAuto) setSemiAuto(false);
        messageApi.info(`匹配结果：${DECISION_LABEL[result.decision] ?? result.decision}`);
      }
    } catch (err) {
      setError(apiErrorMessage(err));
      if (semiAuto) setSemiAuto(false);
    } finally {
      setBusy(false);
    }
  }, [jobId, resumeVersionId, messageApi, semiAuto]);

  const handlePrepare = useCallback(async () => {
    if (!jobId || !resumeVersionId || !matchArtifactId) {
      setError("缺少必要参数（职位/简历/匹配结果），无法准备");
      return;
    }
    setBusy(true);
    setError(null);
    setLastAgentRunId(null);
    try {
      const result = await prepareCommunicate(
        jobId,
        resumeVersionId,
        matchArtifactId,
      );
      setPrepareResult(result);
      setStep("approve");
      messageApi.success("沟通动作已创建，等待审批");
    } catch (err) {
      setError(apiErrorMessage(err));
      if (semiAuto) setSemiAuto(false);
    } finally {
      setBusy(false);
    }
  }, [jobId, resumeVersionId, matchArtifactId, messageApi, semiAuto]);

  const handleApprove = useCallback(async () => {
    if (!actionId || !applicationId) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await approveApplicationAction(applicationId, actionId);
      // Merge the approved action back into prepareResult.
      if (prepareResult) {
        setPrepareResult({ ...prepareResult, action: updated });
      }
      setStep("execute");
      messageApi.success("已批准，可执行发送");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [actionId, applicationId, prepareResult, messageApi]);

  const handleExecute = useCallback(async () => {
    if (!jobId || !actionId || !applicationId) return;
    setBusy(true);
    setError(null);
    setLastAgentRunId(null);
    try {
      const result = await executeCommunicate(jobId, actionId, applicationId);
      setExecuteResult(result);
      setStep("done");
      const rs = result.action.external_result_status;
      if (rs === "submitted") {
        messageApi.success("消息已发送");
      } else if (rs === "duplicate") {
        messageApi.warning("检测到重复沟通");
      } else if (rs === "unknown") {
        messageApi.warning("发送结果未知，需人工核对");
      } else if (rs === "failed") {
        messageApi.error("发送失败");
      }
      onAfterChange();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [jobId, actionId, applicationId, messageApi, onAfterChange]);

  const handleReset = useCallback(() => {
    setStep("inspect");
    setInspectResult(null);
    setMatchResult(null);
    setPrepareResult(null);
    setExecuteResult(null);
    setOpeningMessage("");
    setError(null);
    setLastAgentRunId(null);
    autoFiredRef.current = "";
  }, []);

  // --- render helpers ---------------------------------------------------

  const currentStepIndex = stepIndex(step);
  const hasTerminalResult = action?.external_result_status != null;
  const isApproved = action?.status === "approved";

  return (
    <Card
      type="inner"
      title="BOSS 推荐职位引导沟通"
      size="small"
      extra={
        <Space>
          <Text type="secondary">半自动模式</Text>
          <Switch
            checked={semiAuto}
            onChange={(checked) => {
              setSemiAuto(checked);
              autoFiredRef.current = "";
            }}
            disabled={busy || hasTerminalResult}
          />
        </Space>
      }
    >
      {contextHolder}
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {/* Safety notice */}
        <Alert
          type="info"
          showIcon
          message="半自动模式仅自动执行读取/匹配/准备，发送操作始终需要人工确认"
          description={
            semiAuto
              ? "半自动模式已开启：系统将自动执行「读取职位→匹配分析→准备沟通」，完成后停在审批步骤等待你确认。"
              : "手动模式：每步都需要你点击按钮。开启半自动模式可自动串联前三个步骤。"
          }
        />

        {/* Bridge status */}
        <BridgeStatusCard status={bridgeStatus} connected={bridgeConnected} />

        {/* Steps progress */}
        <Steps
          size="small"
          current={currentStepIndex}
          items={[
            { title: "读取职位" },
            { title: "匹配分析" },
            { title: "准备沟通" },
            { title: "人工审批" },
            { title: "执行发送" },
          ]}
        />

        {/* Error display */}
        {error ? (
          <Alert
            type="error"
            showIcon
            message="操作失败"
            description={
              <Space direction="vertical" size={4}>
                <Text>{error}</Text>
                {lastAgentRunId ? (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    追踪 ID：<Text code>{lastAgentRunId}</Text>（可在 Agent Runs 详情中查看）
                  </Text>
                ) : null}
              </Space>
            }
            closable
            onClose={() => setError(null)}
          />
        ) : null}

        {/* Step 1: Inspect */}
        {(step === "inspect" || inspectResult) && (
          <InspectStepCard
            result={inspectResult}
            busy={busy}
            semiAuto={semiAuto}
            bridgeConnected={bridgeConnected}
            onInspect={handleInspect}
          />
        )}

        {/* Step 2: Match */}
        {(step === "match" || matchResult) && inspectResult?.inspect_status === "ok" && (
          <MatchStepCard
            result={matchResult}
            openingMessage={openingMessage}
            busy={busy}
            semiAuto={semiAuto}
            canRun={!!jobId && !!resumeVersionId}
            onMatch={handleMatch}
            onOpeningMessageChange={setOpeningMessage}
          />
        )}

        {/* Step 3+4: Prepare & Approve */}
        {(step === "prepare" || step === "approve" || prepareResult) && (matchResult?.decision === "communicate" || matchResult?.decision === "needs_review") && (
          <PrepareApproveStepCard
            prepareResult={prepareResult}
            step={step}
            busy={busy}
            semiAuto={semiAuto}
            canPrepare={!!jobId && !!resumeVersionId && !!matchArtifactId}
            canApprove={!!actionId && action?.status === "approval_required"}
            onPrepare={handlePrepare}
            onApprove={handleApprove}
          />
        )}

        {/* Step 5: Execute */}
        {(step === "execute" || executeResult) && isApproved && (
          <ExecuteStepCard
            executeResult={executeResult}
            action={action}
            busy={busy}
            hasTerminalResult={hasTerminalResult}
            onExecute={handleExecute}
          />
        )}

        {/* Reset */}
        {step === "done" || hasTerminalResult ? (
          <Space>
            <Button onClick={handleReset}>重新开始</Button>
          </Space>
        ) : null}
      </Space>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function BridgeStatusCard({
  status,
  connected,
}: {
  status: BridgeStatusResponse | null;
  connected: boolean;
}) {
  return (
    <Descriptions column={2} size="small">
      <Descriptions.Item label="油猴桥接">
        <Tag color={connected ? "green" : "default"}>
          {connected ? "已连接" : "未连接"}
        </Tag>
      </Descriptions.Item>
      <Descriptions.Item label="page_id">
        {status?.page_id ? (
          <Text code>{status.page_id}</Text>
        ) : (
          <Text type="secondary">-</Text>
        )}
      </Descriptions.Item>
      {status?.page_title ? (
        <Descriptions.Item label="当前页面" span={2}>
          <Text type="secondary">
            {status.page_title}
            {status.page_url_hash ? ` (${status.page_url_hash})` : ""}
          </Text>
        </Descriptions.Item>
      ) : null}
    </Descriptions>
  );
}

function InspectStepCard({
  result,
  busy,
  semiAuto,
  bridgeConnected,
  onInspect,
}: {
  result: InspectJobOut | null;
  busy: boolean;
  semiAuto: boolean;
  bridgeConnected: boolean;
  onInspect: () => void;
}) {
  const job = result?.job ?? null;
  return (
    <Card type="inner" size="small" title="步骤 1：读取当前职位">
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Space wrap>
          <Button
            type="primary"
            loading={busy}
            disabled={busy || (!bridgeConnected && !result) || semiAuto}
            onClick={onInspect}
          >
            读取职位
          </Button>
          {!bridgeConnected ? (
            <Text type="warning">
              未检测到油猴脚本，请安装并启用 boss-userscript.user.js，然后在 BOSS 直聘打开职位详情页
            </Text>
          ) : null}
        </Space>

        {result ? (
          <>
            <Tag
              color={
                result.inspect_status === "ok"
                  ? "green"
                  : result.inspect_status === "jd_too_sparse"
                    ? "orange"
                    : "red"
              }
            >
              {INSPECT_LABEL[result.inspect_status] ?? result.inspect_status}
            </Tag>
            {result.message ? (
              <Text type="secondary">{result.message}</Text>
            ) : null}
            {job ? <JobPreview job={job} /> : null}
          </>
        ) : null}
      </Space>
    </Card>
  );
}

function JobPreview({ job }: { job: JobOut }) {
  return (
    <Descriptions column={2} size="small" bordered>
      <Descriptions.Item label="公司">{job.company}</Descriptions.Item>
      <Descriptions.Item label="职位">{job.title}</Descriptions.Item>
      <Descriptions.Item label="城市">{job.location ?? "-"}</Descriptions.Item>
      <Descriptions.Item label="薪资">{job.salary_range ?? "-"}</Descriptions.Item>
      <Descriptions.Item label="职位 ID">
        <Text code>{job.id}</Text>
      </Descriptions.Item>
      <Descriptions.Item label="平台">{job.platform}</Descriptions.Item>
      <Descriptions.Item label="JD 详情" span={2}>
        <Paragraph
          style={{ whiteSpace: "pre-wrap", margin: 0, maxHeight: 200, overflow: "auto" }}
          type="secondary"
        >
          {job.jd_raw}
        </Paragraph>
      </Descriptions.Item>
    </Descriptions>
  );
}

function MatchStepCard({
  result,
  openingMessage,
  busy,
  semiAuto,
  canRun,
  onMatch,
  onOpeningMessageChange,
}: {
  result: MatchDecisionOut | null;
  openingMessage: string;
  busy: boolean;
  semiAuto: boolean;
  canRun: boolean;
  onMatch: () => void;
  onOpeningMessageChange: (v: string) => void;
}) {
  return (
    <Card type="inner" size="small" title="步骤 2：匹配分析">
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Space wrap>
          <Button
            type="primary"
            loading={busy}
            disabled={busy || !canRun || semiAuto}
            onClick={onMatch}
          >
            匹配分析
          </Button>
          {!canRun ? <Text type="warning">缺少职位 ID 或简历版本</Text> : null}
        </Space>

        {result ? (
          <>
            <Space>
              <Tag color={DECISION_COLOR[result.decision] ?? "default"}>
                {DECISION_LABEL[result.decision] ?? result.decision}
              </Tag>
              <Text type="secondary">评分：{result.score.toFixed(2)}</Text>
            </Space>

            {result.reasons.length > 0 ? (
              <div>
                <Text strong>理由：</Text>
                <ul style={{ margin: "4px 0", paddingLeft: 20 }}>
                  {result.reasons.map((r, i) => (
                    <li key={i}>
                      <Text type="secondary">{r}</Text>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {result.risks.length > 0 ? (
              <div>
                <Text strong>风险：</Text>
                <ul style={{ margin: "4px 0", paddingLeft: 20 }}>
                  {result.risks.map((r, i) => (
                    <li key={i}>
                      <Text type="warning">{r}</Text>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {result.message ? (
              <Alert type="info" showIcon message={result.message} />
            ) : null}

            {result.decision === "needs_review" ? (
              <Alert
                type="warning"
                showIcon
                message="需人工审阅"
                description="匹配评分一般或存在风险项。你可以查看上方风险与缺失要求，确认后点击「准备沟通」继续，或放弃此职位。"
              />
            ) : null}

            {result.missing_requirements.length > 0 ? (
              <div>
                <Text strong>缺失要求：</Text>
                <ul style={{ margin: "4px 0", paddingLeft: 20 }}>
                  {result.missing_requirements.map((r, i) => (
                    <li key={i}>
                      <Text type="danger">{r}</Text>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {(result.decision === "communicate" || result.decision === "needs_review") && openingMessage ? (
              <div>
                <Text strong>开场白（可编辑）：</Text>
                <Input.TextArea
                  style={{ marginTop: 4 }}
                  value={openingMessage}
                  onChange={(e) => onOpeningMessageChange(e.target.value)}
                  autoSize={{ minRows: 3, maxRows: 8 }}
                  disabled={busy}
                />
                <Text type="secondary" style={{ fontSize: 12 }}>
                  注意：编辑开场白仅影响本地预览。后端 prepare 阶段使用匹配产物中存储的开场白。
                </Text>
              </div>
            ) : null}
          </>
        ) : null}
      </Space>
    </Card>
  );
}

function PrepareApproveStepCard({
  prepareResult,
  step,
  busy,
  semiAuto,
  canPrepare,
  canApprove,
  onPrepare,
  onApprove,
}: {
  prepareResult: CommunicatePrepareOut | null;
  step: PilotStep;
  busy: boolean;
  semiAuto: boolean;
  canPrepare: boolean;
  canApprove: boolean;
  onPrepare: () => void;
  onApprove: () => void;
}) {
  const action = prepareResult?.action ?? null;
  return (
    <Card type="inner" size="small" title="步骤 3-4：准备沟通 & 人工审批">
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        {/* Prepare button — only show if not yet prepared */}
        {!action ? (
          <Space wrap>
            <Button
              type="primary"
              loading={busy}
              disabled={busy || !canPrepare || semiAuto}
              onClick={onPrepare}
            >
              准备沟通
            </Button>
            {!canPrepare ? <Text type="warning">缺少必要参数</Text> : null}
          </Space>
        ) : null}

        {action ? (
          <>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="动作 ID">
                <Text code>{action.id}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={action.status === "approved" ? "green" : "orange"}>
                  {action.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="动作类型">
                {action.action_type}
              </Descriptions.Item>
              <Descriptions.Item label="应用 ID">
                <Text code>{action.application_id}</Text>
              </Descriptions.Item>
              {action.payload_preview.outgoing_text ? (
                <Descriptions.Item label="开场白" span={2}>
                  <Paragraph
                    style={{ whiteSpace: "pre-wrap", margin: 0, background: "#fafafa", padding: 8, borderRadius: 4 }}
                  >
                    {action.payload_preview.outgoing_text}
                  </Paragraph>
                </Descriptions.Item>
              ) : null}
            </Descriptions>

            {/* Approve button — shown when in approve step or action is approval_required */}
            {step === "approve" || action.status === "approval_required" ? (
              <Space wrap>
                <Popconfirm
                  title="批准此沟通动作？"
                  description="批准后将可执行发送。"
                  onConfirm={onApprove}
                  disabled={busy || !canApprove}
                >
                  <Button type="primary" loading={busy} disabled={busy || !canApprove}>
                    批准沟通
                  </Button>
                </Popconfirm>
                {!canApprove ? (
                  <Text type="secondary">动作状态不是「待审批」，无法批准</Text>
                ) : null}
              </Space>
            ) : null}

            {action.status === "approved" ? (
              <Alert
                type="success"
                showIcon
                message="已批准"
                description="动作已批准，可在下方执行发送。"
              />
            ) : null}
          </>
        ) : null}
      </Space>
    </Card>
  );
}

function ExecuteStepCard({
  executeResult,
  action,
  busy,
  hasTerminalResult,
  onExecute,
}: {
  executeResult: CommunicateExecuteOut | null;
  action: ApplicationActionOut | null;
  busy: boolean;
  hasTerminalResult: boolean;
  onExecute: () => void;
}) {
  const resultStatus = (executeResult?.action ?? action)?.external_result_status;
  return (
    <Card type="inner" size="small" title="步骤 5：执行发送">
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Alert
          type="warning"
          showIcon
          message="此操作将真实发送消息至 BOSS 平台，不可撤销"
          description="点击「执行发送」后，系统将通过油猴脚本点击「立即沟通」并发送开场白。"
        />

        <Space wrap>
          <Popconfirm
            title="确认执行发送？"
            description="此操作将真实发送消息，不可撤销。"
            onConfirm={onExecute}
            disabled={busy || hasTerminalResult}
          >
            <Button
              type="primary"
              danger
              loading={busy}
              disabled={busy || hasTerminalResult}
            >
              执行发送
            </Button>
          </Popconfirm>
        </Space>

        {executeResult ? (
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="发送结果">
              {resultStatus ? (
                <Tag color={EXECUTE_RESULT_COLOR[resultStatus] ?? "default"}>
                  {EXECUTE_RESULT_LABEL[resultStatus] ?? resultStatus}
                </Tag>
              ) : (
                <Text type="secondary">未执行</Text>
              )}
            </Descriptions.Item>
            {executeResult.message ? (
              <Descriptions.Item label="信息">
                {executeResult.message}
              </Descriptions.Item>
            ) : null}
          </Descriptions>
        ) : null}

        {hasTerminalResult ? (
          <Text type="secondary">
            外部结果已记录，无法重复执行。如需重试，请点击「重新开始」。
          </Text>
        ) : null}
      </Space>
    </Card>
  );
}
