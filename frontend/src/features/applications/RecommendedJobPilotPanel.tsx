import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
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
  HumanReviewOverride,
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
  // Demo mode: when the application already has a job_id (seeded data) we
  // start at "match" instead of "inspect". Inspect reads JD via the
  // userscript bridge, which is local-dev only; in the showcase deployment
  // the bridge is always disconnected and the job already exists with JD
  // data, so skipping inspect lets the match → prepare → approve → execute
  // chain proceed.
  const [step, setStep] = useState<PilotStep>(
    application.job_id ? "match" : "inspect",
  );
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

  // human-review override state (needs_review path). The textarea is prefilled
  // with the pre-gate model draft (draft_opening_message) when available.
  const [humanReviewMessage, setHumanReviewMessage] = useState("");
  const [humanReviewAck, setHumanReviewAck] = useState(false);
  const [humanReviewOpen, setHumanReviewOpen] = useState(false);

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
    setStep(application.job_id ? "match" : "inspect");
    setInspectResult(null);
    setMatchResult(null);
    setPrepareResult(null);
    setExecuteResult(null);
    setOpeningMessage("");
    setHumanReviewMessage("");
    setHumanReviewAck(false);
    setHumanReviewOpen(false);
    setError(null);
    setLastAgentRunId(null);
  }, [application.id, application.job_id]);

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
      // Prefill the human-review textarea with the pre-gate draft when the
      // safety gate downgraded the decision (PRD R2). The draft is response-only
      // and never persisted; the user edits it and re-submits via the override.
      //
      // When the gate downgraded but the model produced no draft (e.g. the
      // downgrade was triggered by an invalid/missing opening message rather
      // than low score), the textarea is empty and the user must write one from
      // scratch. We do NOT auto-generate a message — the whole point of the
      // override is that a human takes responsibility for the exact text.
      if (result.draft_opening_message) {
        setHumanReviewMessage(result.draft_opening_message);
      } else {
        setHumanReviewMessage("");
      }
      setHumanReviewAck(false);
      setHumanReviewOpen(false);
      if (result.decision === "communicate") {
        setStep("prepare");
        messageApi.success("匹配通过，建议沟通");
      } else if (result.decision === "needs_review") {
        // needs_review: the backend match safety gate has downgraded the
        // decision (score below threshold / missing requirements / risky
        // opening message). The backend refuses prepare with 422, so we must
        // NOT advance to the prepare step — doing so would surface a button
        // (prepare) that only ever errors. Stop the semi-auto loop and hand
        // over to the manual human-review area: when the loop was running we
        // expand the area so the stop point is visibly a human-review step.
        setStep("match");
        if (semiAuto) {
          setSemiAuto(false);
          setHumanReviewOpen(true);
        }
        messageApi.warning("安全门拦截：当前匹配结果不允许准备沟通，请查看拦截说明");
      } else {
        // skip — hard stop for the auto path, but like needs_review the
        // human may take over: stop the loop and hand over to the manual
        // human-review area (expanded), with a stronger warning in the UI.
        setStep("match");
        if (semiAuto) {
          setSemiAuto(false);
          setHumanReviewOpen(true);
        }
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

  const handleHumanReviewPrepare = useCallback(async () => {
    if (!jobId || !resumeVersionId || !matchArtifactId) {
      setError("缺少必要参数（职位/简历/匹配结果），无法准备");
      return;
    }
    const msg = humanReviewMessage.trim();
    if (!msg || !humanReviewAck) {
      setError("请填写开场白并勾选确认后再继续");
      return;
    }
    // Frontend gate: mirror the backend's validate_opening_message minimum
    // (10 chars) so the user gets immediate feedback instead of a 422 round-
    // trip. The backend still re-validates; this is UX-only.
    if (msg.length < 10) {
      setError("开场白过短，至少 10 个字");
      return;
    }
    // draft_source: if the user left the prefilled model draft unchanged,
    // record "model_draft"; any edit (or new text) is "human_written".
    const draftSource: HumanReviewOverride["draft_source"] =
      matchResult?.draft_opening_message &&
      msg === matchResult.draft_opening_message.trim()
        ? "model_draft"
        : "human_written";
    setBusy(true);
    setError(null);
    setLastAgentRunId(null);
    try {
      const result = await prepareCommunicate(jobId, resumeVersionId, matchArtifactId, {
        opening_message: msg,
        acknowledged: true,
        draft_source: draftSource,
      });
      setPrepareResult(result);
      setStep("approve");
      messageApi.success("人工审阅已通过，沟通动作已创建，等待审批");
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [
    jobId,
    resumeVersionId,
    matchArtifactId,
    humanReviewMessage,
    humanReviewAck,
    matchResult,
    messageApi,
  ]);

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
    setStep(application.job_id ? "match" : "inspect");
    setInspectResult(null);
    setMatchResult(null);
    setPrepareResult(null);
    setExecuteResult(null);
    setOpeningMessage("");
    setHumanReviewMessage("");
    setHumanReviewAck(false);
    setHumanReviewOpen(false);
    setError(null);
    setLastAgentRunId(null);
    autoFiredRef.current = "";
  }, [application.job_id]);

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

        {/* Step 1: Inspect
            Hidden in demo mode when the job is already seeded (jobId present
            with no inspect result) — the bridge is not connected in the
            showcase deployment, so inspect would always fail. */}
        {(step === "inspect" || inspectResult) &&
         !(!!jobId && !inspectResult) && (
          <InspectStepCard
            result={inspectResult}
            busy={busy}
            semiAuto={semiAuto}
            bridgeConnected={bridgeConnected}
            onInspect={handleInspect}
          />
        )}

        {/* Step 2: Match
            In the demo flow (bridge disconnected, seeded job), inspect is
            skipped — the job already exists with JD data. We show the match
            card when step is "match" OR matchResult exists, as long as the
            job is ready (either via inspect success or a pre-seeded job_id). */}
        {(step === "match" || matchResult) &&
         (inspectResult?.inspect_status === "ok" || (!inspectResult && !!jobId)) && (
          <MatchStepCard
            result={matchResult}
            openingMessage={openingMessage}
            busy={busy}
            semiAuto={semiAuto}
            canRun={!!jobId && !!resumeVersionId}
            onMatch={handleMatch}
            humanReviewOpen={humanReviewOpen}
            humanReviewMessage={humanReviewMessage}
            humanReviewAck={humanReviewAck}
            onHumanReviewOpenChange={setHumanReviewOpen}
            onHumanReviewMessageChange={setHumanReviewMessage}
            onHumanReviewAckChange={setHumanReviewAck}
            onHumanReviewPrepare={handleHumanReviewPrepare}
            hasDraft={!!matchResult?.draft_opening_message}
            canPrepare={!!jobId && !!resumeVersionId && !!matchArtifactId}
          />
        )}

        {/* Step 3+4: Prepare & Approve — shown for a clear "communicate"
            decision, OR after a successful human-review override (needs_review
            → override prepare → approve step). In the override case the
            matchResult.decision is still needs_review, but prepareResult is
            set and step advanced to "approve", so we gate on that too. */}
        {((step === "prepare" || step === "approve" || prepareResult) &&
          (matchResult?.decision === "communicate" || prepareResult != null)) ? (
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
        ) : null}

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
  humanReviewOpen,
  humanReviewMessage,
  humanReviewAck,
  onHumanReviewOpenChange,
  onHumanReviewMessageChange,
  onHumanReviewAckChange,
  onHumanReviewPrepare,
  hasDraft,
  canPrepare,
}: {
  result: MatchDecisionOut | null;
  openingMessage: string;
  busy: boolean;
  semiAuto: boolean;
  canRun: boolean;
  onMatch: () => void;
  humanReviewOpen: boolean;
  humanReviewMessage: string;
  humanReviewAck: boolean;
  onHumanReviewOpenChange: (open: boolean) => void;
  onHumanReviewMessageChange: (msg: string) => void;
  onHumanReviewAckChange: (ack: boolean) => void;
  onHumanReviewPrepare: () => void;
  hasDraft: boolean;
  canPrepare: boolean;
}) {
  // The human-review message is submittable only when non-empty AND meets the
  // backend's 10-char minimum (validate_opening_message). Pre-compute so the
  // button + Popconfirm share one disabled gate — without this the user can
  // click through with a too-short message and get a 422.
  const humanReviewMessageReady =
    humanReviewMessage.trim().length >= 10;
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
                type="error"
                showIcon
                message="安全门拦截：当前不允许自动准备沟通"
                description={
                  <Space direction="vertical" size={6}>
                    <Text>
                      匹配结果为「需人工审阅」，安全门已拦截自动准备。原因可能是评分低于阈值、缺失关键要求或开场白命中风险项。
                    </Text>
                    <Text type="secondary">当前评分：{result.score.toFixed(2)}</Text>
                    {result.risks.length > 0 ? (
                      <Text type="warning">风险项：{result.risks.join("；")}</Text>
                    ) : null}
                    {result.missing_requirements.length > 0 ? (
                      <Text type="danger">缺失要求：{result.missing_requirements.join("；")}</Text>
                    ) : null}
                    <Space wrap>
                      <Button size="small" onClick={onMatch} loading={busy} disabled={busy || !canRun}>
                        重新匹配
                      </Button>
                    </Space>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      出路：更换职位、调整简历后重新匹配，放弃此职位，或进行人工审阅覆写。
                    </Text>
                  </Space>
                }
              />
            ) : null}

            {/* Human-review override area (PRD R3/R6). Rendered for every
                blocked decision (needs_review AND skip), including ones
                produced while semi-auto was running: the loop stops itself
                and hands over to this manual area. The auto path never
                submits human_review — the override is only sent via the
                explicit human click below, so showing the area cannot bypass
                the safety gate. skip carries a stronger responsibility
                warning (the model flagged a clear non-match). The area is
                collapsible and collapsed by default (expanded on a semi-auto
                handover) so it does not compete with the "放弃此职位" exit. */}
            {result.decision === "needs_review" || result.decision === "skip" ? (
              <Card
                type="inner"
                size="small"
                style={{ marginTop: 4 }}
                title={
                  <Space>
                    <Text strong>人工审阅覆写</Text>
                    <Tag color="orange">需人工担责</Tag>
                  </Space>
                }
              >
                <Space direction="vertical" size="small" style={{ width: "100%" }}>
                  <Alert
                    type="warning"
                    showIcon
                    message={
                      result.decision === "skip"
                        ? "覆写「跳过」判定：模型认为该职位明确不匹配，人工覆写需完全自行担责"
                        : "覆写是人工显式担责路径，绕过安全门的自动拦截"
                    }
                    description={
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        你需要自行确认风险可接受，并对该沟通决定负责。系统仍会复验开场白的长度/PII/语气，且发送前必须经审批。
                      </Text>
                    }
                  />
                  <Space>
                    <Button
                      size="small"
                      type={humanReviewOpen ? "default" : "link"}
                      onClick={() => onHumanReviewOpenChange(!humanReviewOpen)}
                    >
                      {humanReviewOpen ? "收起人审区" : "展开人工审阅区"}
                    </Button>
                  </Space>
                  {humanReviewOpen ? (
                    <>
                      <div>
                        <Text strong>开场白（可编辑）</Text>
                        <Input.TextArea
                          value={humanReviewMessage}
                          onChange={(e) => onHumanReviewMessageChange(e.target.value)}
                          autoSize={{ minRows: 3, maxRows: 8 }}
                          placeholder="填写要发送的开场白（10-500 字，不含手机号/邮箱/身份证号）"
                          style={{ marginTop: 4 }}
                        />
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {hasDraft
                            ? "已预填模型草稿仅供参考，发送内容以审批预览为准。"
                            : "模型未提供草稿，请自行撰写开场白。"}
                        </Text>
                      </div>
                      <Checkbox
                        checked={humanReviewAck}
                        onChange={(e) => onHumanReviewAckChange(e.target.checked)}
                      >
                        我已审阅上述风险与缺失要求，确认以本人名义继续准备沟通，并对该决定负责
                      </Checkbox>
                      <Space wrap>
                        <Popconfirm
                          title="确认人工审阅后继续准备沟通？"
                          description="此操作将绕过安全门的自动拦截，以你确认的开场白创建待审批的沟通动作。"
                          onConfirm={onHumanReviewPrepare}
                          disabled={busy || !humanReviewAck || !humanReviewMessageReady || !canPrepare}
                        >
                          <Button
                            type="primary"
                            loading={busy}
                            disabled={
                              busy ||
                              !humanReviewAck ||
                              !humanReviewMessageReady ||
                              !canPrepare
                            }
                          >
                            人工审阅后继续
                          </Button>
                        </Popconfirm>
                        {!humanReviewAck ? (
                          <Text type="secondary">请先勾选确认</Text>
                        ) : null}
                        {humanReviewMessage.trim().length > 0 &&
                        humanReviewMessage.trim().length < 10 ? (
                          <Text type="warning">
                            开场白过短（{humanReviewMessage.trim().length}/10 字起）
                          </Text>
                        ) : null}
                        {!humanReviewMessage.trim() ? (
                          <Text type="secondary">请填写开场白</Text>
                        ) : null}
                      </Space>
                    </>
                  ) : null}
                </Space>
              </Card>
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

            {result.decision === "communicate" && openingMessage ? (
              <div>
                <Text strong>开场白：</Text>
                <Paragraph
                  style={{ marginTop: 4, marginBottom: 0, whiteSpace: "pre-wrap" }}
                  copyable={{ text: openingMessage }}
                >
                  {openingMessage}
                </Paragraph>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  发送内容以审批预览为准；如需调整请重新匹配。
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
