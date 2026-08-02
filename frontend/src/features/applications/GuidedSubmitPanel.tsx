import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  apiErrorMessage,
  abortPlatformSubmission,
  listApplicationActions,
  preparePlatformSubmission,
  submitPlatformSubmission,
} from "@/api/client";
import { useAgentRunPolling } from "@/features/agent-runs/useAgentRunPolling";
import {
  AGENT_RUN_STATUS_COLOR,
  AGENT_RUN_STATUS_LABEL,
} from "@/features/agent-runs/status";
import {
  APP_STATUS_LABEL,
  normalizeApplicationStatus,
  type ApplicationStatus,
} from "./status";
import type {
  AgentRunDetailOut,
  ApplicationActionOutFull,
  ApplicationOut,
  ExternalActionResultStatus,
  PlatformSubmissionPrepareResponse,
} from "@/types";

const { Text, Paragraph } = Typography;

/**
 * Manual control labels for the post-submit result. The backend persists the
 * real terminal result; these controls exist so the user can surface a result
 * when the synchronous submit could not classify it (design.md §Frontend Flow:
 * "manual result controls: mark submitted, mark duplicate, mark unknown, abort").
 *
 * NOTE: the manual controls are intentionally limited to surfacing an *abort*
 * (revoke the action so it can no longer authorize execution). The backend has
 * no "manually mark submitted/duplicate/unknown" endpoint — confirmed results
 * come only from the adapter. The copy explains this so the user is not misled
 * into thinking the buttons perform the platform action.
 */
const EXTERNAL_RESULT_LABEL: Record<ExternalActionResultStatus, string> = {
  submitted: "已投递",
  duplicate: "重复投递",
  unknown: "结果未知",
  failed: "平台失败",
};

const EXTERNAL_RESULT_COLOR: Record<ExternalActionResultStatus, string> = {
  submitted: "green",
  duplicate: "gold",
  unknown: "volcano",
  failed: "red",
};

/** Application statuses from which a prepare run may be requested. */
const PREPARABLE_STATUSES: ReadonlySet<ApplicationStatus> = new Set([
  "materials_ready",
  "approval_required",
  "approved",
]);

/** Format an ISO timestamp (or null) for compact display. */
function formatTime(ts: string | null): string {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

interface Props {
  application: ApplicationOut;
  /** Called after any state-changing action so the parent refreshes detail. */
  onAfterChange: () => void;
}

/**
 * Guided platform-submit panel (design.md §Frontend Flow).
 *
 * Two-phase, approval-gated flow:
 *   1. Prepare — runs the BOSS adapter in dry-run/fill-only mode via an agent
 *      run the frontend polls to completion. The persisted
 *      ``ApplicationAction`` carries the sanitized filled preview.
 *   2. Submit — runs synchronously behind the approval + idempotency guards.
 *      The final-submit button is disabled until the action status is
 *      ``approved``.
 *
 * Copy is explicit that the system is preparing a draft and will NOT submit
 * without approval. No raw credentials, session data, cookies, tokens, or page
 * HTML are ever displayed — only the sanitized payload preview the user is
 * approving.
 */
export function GuidedSubmitPanel({ application, onAfterChange }: Props) {
  const status = normalizeApplicationStatus(application.status);
  const [runId, setRunId] = useState<string | null>(null);
  const [prepareResp, setPrepareResp] =
    useState<PlatformSubmissionPrepareResponse | null>(null);
  const [action, setAction] = useState<ApplicationActionOutFull | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [aborting, setAborting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  // Form inputs for the prepare request. The target resource is the platform
  // page the form lives on; the outgoing text is the HR message the adapter
  // will fill into the form in dry-run mode.
  const [targetResource, setTargetResource] = useState("");
  const [outgoingText, setOutgoingText] = useState("");

  // Poll the active prepare run to completion. On terminal, hydrate the
  // prepared action from the application action list so the final-submit button
  // can become available after the sibling approval panel approves it.
  const refreshLatestAction = useCallback(async () => {
    try {
      const data = await listApplicationActions(application.id);
      const latest = data.items.find(
        (item) =>
          item.action_type === "platform_submit" &&
          (item.payload_preview.target_platform ?? "boss") === "boss",
      );
      setAction((latest as ApplicationActionOutFull | undefined) ?? null);
    } catch (err) {
      messageApi.warning(apiErrorMessage(err));
    }
  }, [application.id, messageApi]);

  const onTerminal = useCallback(
    (detail: AgentRunDetailOut) => {
      if (detail.status === "failed") {
        messageApi.error(
          `准备失败${detail.error ? `：${detail.error}` : ""}`,
        );
      } else if (detail.status === "succeeded") {
        messageApi.success("平台投递草稿已生成，请审批后确认投递");
        void refreshLatestAction();
        onAfterChange();
      }
    },
    [messageApi, onAfterChange, refreshLatestAction],
  );

  const { detail, polling } = useAgentRunPolling(runId, {
    onTerminal,
  });

  // Reflect the polled run status back into the prepare response card so the
  // user sees live progress.
  const liveRunStatus = detail?.status ?? prepareResp?.run.status ?? null;

  const handlePrepare = async () => {
    if (!targetResource.trim()) {
      messageApi.error("请填写目标资源（如职位链接或 HR 会话 ID）");
      return;
    }
    try {
      setSubmitting(true);
      setAction(null);
      const resp = await preparePlatformSubmission(application.id, {
        target_resource: targetResource.trim(),
        selected_artifact_ids: [],
        outgoing_text: outgoingText.trim() || null,
        resume_file_reference: application.resume_version_id,
      });
      setPrepareResp(resp);
      setRunId(resp.run.id);
      messageApi.info("已提交准备请求，正在生成投递草稿…");
    } catch (err) {
      messageApi.error(apiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleFinalSubmit = async () => {
    if (!runId) return;
    try {
      setSubmitting(true);
      const resp = await submitPlatformSubmission(application.id, runId);
      setAction(resp.action);
      const rs = resp.action.external_result_status;
      if (rs === "submitted") {
        messageApi.success("已确认投递成功");
      } else if (rs === "duplicate") {
        messageApi.warning("平台检测到重复投递，请人工核对");
      } else if (rs === "unknown") {
        messageApi.warning("投递结果未知，需人工核对");
      } else if (rs === "failed") {
        messageApi.error("平台投递失败，请查看失败信息");
      }
      onAfterChange();
    } catch (err) {
      messageApi.error(apiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleAbort = async () => {
    if (!runId) return;
    try {
      setAborting(true);
      const resp = await abortPlatformSubmission(application.id, runId);
      setAction(resp.action);
      messageApi.success("已撤销投递授权");
      onAfterChange();
    } catch (err) {
      messageApi.error(apiErrorMessage(err));
    } finally {
      setAborting(false);
    }
  };

  // Reset internal state when the application id changes (user switched
  // application records).
  useEffect(() => {
    setRunId(null);
    setPrepareResp(null);
    setAction(null);
    setTargetResource("");
    setOutgoingText("");
  }, [application.id]);

  // When the sibling approval panel approves/revokes the action it refreshes
  // the parent application detail. Use that updated application timestamp as a
  // cheap signal to hydrate the latest platform_submit action here.
  useEffect(() => {
    void refreshLatestAction();
  }, [application.id, application.updated_at, refreshLatestAction]);

  const canPrepare = PREPARABLE_STATUSES.has(status);
  // The final-submit button is disabled until the action status is ``approved``
  // (design.md §Frontend Flow). The approval itself happens in the sibling
  // ``ApplicationActionsPanel``; here we only gate on the action status.
  const actionStatus = action?.status ?? null;
  const canFinalSubmit = actionStatus === "approved";
  const hasTerminalResult = action?.external_result_status != null;

  return (
    <Card type="inner" title="平台引导投递（BOSS）" size="small">
      {contextHolder}
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Alert
          type="warning"
          showIcon
          message="系统仅准备投递草稿，未经审批不会投递"
          description="点击「准备投递草稿」后，系统以只填表单、不提交的方式生成草稿。你必须在外部动作审批区批准后，「确认投递」按钮才会启用。系统不会自动登录、不会保存登录态、不会绕过验证码。"
        />

        {!canPrepare ? (
          <Text type="secondary">
            当前状态为「{APP_STATUS_LABEL[status]}」，仅在「材料就绪 / 待审批 /
            已批准」状态下可生成投递草稿。
          </Text>
        ) : null}

        <Descriptions column={1} size="small">
          <Descriptions.Item label="目标资源">
            <Input
              style={{ width: "100%" }}
              placeholder="如职位链接或 HR 会话 ID"
              value={targetResource}
              onChange={(e) => setTargetResource(e.target.value)}
              disabled={!canPrepare || submitting || polling}
            />
          </Descriptions.Item>
          <Descriptions.Item label="外发文本（可选）">
            <Input.TextArea
              style={{ width: "100%" }}
              placeholder="即将填入平台表单的开场话术内容"
              value={outgoingText}
              onChange={(e) => setOutgoingText(e.target.value)}
              autoSize={{ minRows: 2, maxRows: 6 }}
              disabled={!canPrepare || submitting || polling}
            />
          </Descriptions.Item>
        </Descriptions>

        <Space wrap>
          <Button
            type="primary"
            loading={submitting && !polling}
            disabled={!canPrepare || polling}
            onClick={handlePrepare}
          >
            准备投递草稿
          </Button>
          {polling ? (
            <Tag color={AGENT_RUN_STATUS_COLOR.running} icon={<Spin size="small" />}>
              运行中
            </Tag>
          ) : null}
        </Space>

        {/* Active prepare-run card: live status from the shared polling hook. */}
        {liveRunStatus ? (
          <Card type="inner" size="small" title="准备任务">
            <Descriptions column={2} size="small">
              <Descriptions.Item label="运行 ID">
                <Text code>{runId ?? "-"}</Text>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={AGENT_RUN_STATUS_COLOR[liveRunStatus as keyof typeof AGENT_RUN_STATUS_COLOR]}>
                  {AGENT_RUN_STATUS_LABEL[liveRunStatus as keyof typeof AGENT_RUN_STATUS_LABEL] ?? liveRunStatus}
                </Tag>
              </Descriptions.Item>
              {detail?.error ? (
                <Descriptions.Item label="错误" span={2}>
                  <Text type="danger">{detail.error}</Text>
                </Descriptions.Item>
              ) : null}
            </Descriptions>
          </Card>
        ) : null}

        {/* Filled preview table: only safe fields the user is approving. */}
        {action ? (
          <FilledPreviewCard action={action} />
        ) : prepareResp && !polling && liveRunStatus === "succeeded" ? (
          <Alert
            type="info"
            showIcon
            message="草稿已生成"
            description="请前往「外部动作审批」区查看并批准草稿，批准后可在此确认投递。"
          />
        ) : prepareResp && !polling && liveRunStatus === "failed" ? (
          <Alert
            type="error"
            showIcon
            message="准备失败"
            description={detail?.error ?? "平台准备阶段失败，请稍后重试或人工检查。"}
          />
        ) : null}

        {/* Final submit / abort controls. */}
        {action ? (
          <Card type="inner" size="small" title="确认投递">
            <Space direction="vertical" size="middle" style={{ width: "100%" }}>
              <Descriptions column={2} size="small">
                <Descriptions.Item label="动作状态">
                  <Tag color={actionStatus === "approved" ? "green" : "orange"}>
                    {actionStatus ?? "-"}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="外部结果">
                  {action.external_result_status ? (
                    <Tag color={EXTERNAL_RESULT_COLOR[action.external_result_status]}>
                      {EXTERNAL_RESULT_LABEL[action.external_result_status]}
                    </Tag>
                  ) : (
                    <Text type="secondary">未执行</Text>
                  )}
                </Descriptions.Item>
                {action.external_result ? (
                  <Descriptions.Item label="执行时间" span={2}>
                    {formatTime(action.external_result.started_at)} →{" "}
                    {formatTime(action.external_result.completed_at)}
                  </Descriptions.Item>
                ) : null}
              </Descriptions>

              <Alert
                type="info"
                showIcon
                message="未审批不会投递"
                description={
                  canFinalSubmit
                    ? "动作已批准，可点击「确认投递」。投递为不可逆外部操作，请再次确认。"
                    : "请先在「外部动作审批」区批准草稿，「确认投递」按钮才会启用。"
                }
              />

              <Space wrap>
                <Popconfirm
                  title="确认执行最终投递？"
                  description="此操作将真实提交至平台，不可撤销。"
                  onConfirm={handleFinalSubmit}
                  disabled={!canFinalSubmit || submitting || hasTerminalResult}
                >
                  <Button
                    type="primary"
                    danger
                    loading={submitting}
                    disabled={!canFinalSubmit || hasTerminalResult}
                  >
                    确认投递
                  </Button>
                </Popconfirm>
                <Popconfirm
                  title="撤销投递授权？"
                  description="撤销后动作将无法授权执行，需重新准备草稿。"
                  onConfirm={handleAbort}
                  disabled={aborting || hasTerminalResult}
                >
                  <Button danger disabled={aborting || hasTerminalResult} loading={aborting}>
                    撤销投递
                  </Button>
                </Popconfirm>
              </Space>

              {hasTerminalResult ? (
                <Text type="secondary">
                  外部结果已记录，无法重复执行或撤销。如需重试，请重新准备草稿。
                </Text>
              ) : null}
            </Space>
          </Card>
        ) : null}
      </Space>
    </Card>
  );
}

/**
 * Sanitized filled-preview table. Shows only the safe visible fields the user
 * is approving — never raw cookies, tokens, session data, or page HTML
 * (design.md §Frontend Flow, agent-safety-sandbox spec).
 *
 * The action's ``payload_preview`` carries the approval-boundary fields. The
 * adapter's full ``FilledSubmissionSnapshot`` (with page_state url hashes etc.)
 * is intentionally NOT mirrored to the frontend — the preview is enough for the
 * user to decide.
 */
function FilledPreviewCard({
  action,
}: {
  action: ApplicationActionOutFull;
}) {
  const { payload_preview, payload_hash, source_snapshot } = action;

  const fieldRows = useMemo(() => {
    // The payload preview does not carry per-field rows (the backend stores the
    // full snapshot server-side). We surface the outgoing text as the single
    // approvable field so the user sees exactly what will be submitted.
    const rows: { key: string; label: string; value: string }[] = [];
    if (payload_preview.outgoing_text) {
      rows.push({
        key: "outgoing_text",
        label: "外发文本",
        value: payload_preview.outgoing_text,
      });
    }
    if (payload_preview.resume_file_reference) {
      rows.push({
        key: "resume_file_reference",
        label: "简历文件引用",
        value: payload_preview.resume_file_reference,
      });
    }
    return rows;
  }, [payload_preview]);

  return (
    <Card type="inner" size="small" title="投递草稿预览（你将审批的内容）">
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Descriptions column={1} size="small">
          <Descriptions.Item label="目标平台">
            {payload_preview.target_platform ?? "-"}
          </Descriptions.Item>
          <Descriptions.Item label="目标资源">
            {payload_preview.target_resource ?? "-"}
          </Descriptions.Item>
          <Descriptions.Item label="选中产物 ID">
            {payload_preview.selected_artifact_ids.length > 0
              ? payload_preview.selected_artifact_ids.join(", ")
              : "-"}
          </Descriptions.Item>
          <Descriptions.Item label="载荷哈希">
            <Text code>{payload_hash}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="来源哈希">
            <Text code>{source_snapshot.source_hash}</Text>
          </Descriptions.Item>
        </Descriptions>

        {fieldRows.length > 0 ? (
          <Table
            size="small"
            pagination={false}
            dataSource={fieldRows}
            columns={[
              { title: "字段", dataIndex: "label", key: "label", width: 160 },
              {
                title: "值",
                dataIndex: "value",
                key: "value",
                render: (v: string) => (
                  <Paragraph
                    style={{
                      whiteSpace: "pre-wrap",
                      margin: 0,
                      background: "#fafafa",
                      padding: 8,
                      borderRadius: 4,
                    }}
                  >
                    {v}
                  </Paragraph>
                ),
              },
            ]}
          />
        ) : (
          <Empty description="无可预览字段" />
        )}
      </Space>
    </Card>
  );
}
