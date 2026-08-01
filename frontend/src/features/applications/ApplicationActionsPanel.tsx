import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import {
  apiErrorMessage,
  approveApplicationAction,
  listApplicationActions,
  previewApplicationAction,
  revokeApplicationAction,
} from "@/api/client";
import type {
  ApplicationActionCreate,
  ApplicationActionOut,
  ExternalActionStatus,
  ExternalActionType,
} from "@/types";

const { Paragraph, Text } = Typography;

const ACTION_TYPE_LABEL: Record<ExternalActionType, string> = {
  platform_submit: "平台投递",
  hr_message: "HR 沟通",
  resume_upload: "简历上传",
  profile_fill: "资料填写",
  follow_up_message: "跟进消息",
};

const STATUS_LABEL: Record<ExternalActionStatus, string> = {
  draft: "草稿",
  approval_required: "待审批",
  approved: "已批准",
  stale: "已过期",
  revoked: "已撤销",
  blocked: "已阻止",
};

const STATUS_COLOR: Record<ExternalActionStatus, string> = {
  draft: "default",
  approval_required: "orange",
  approved: "green",
  stale: "volcano",
  revoked: "red",
  blocked: "magenta",
};

/** Format an ISO timestamp (or null) for compact display. */
function formatTime(ts: string | null): string {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

interface Props {
  applicationId: string;
  /** Readiness source hash to embed in new action previews. */
  sourceHash: string | null;
  /** Resume version id bound to the application (for source snapshot). */
  resumeVersionId: string | null;
  /** Job id bound to the application (for source snapshot). */
  jobId: string;
}

/**
 * Panel for previewing, approving, and revoking planned external actions.
 *
 * The panel deliberately has NO "execute" / "submit" button — the backend
 * exposes no execution endpoint yet. Future platform tools must call the
 * approval-boundary guard (``assert_action_approved``) before touching any
 * platform. This UI only prepares the boundary: the user sees the exact
 * payload, approves it, and can revoke at any time.
 */
export function ApplicationActionsPanel({
  applicationId,
  sourceHash,
  resumeVersionId,
  jobId,
}: Props) {
  const [actions, setActions] = useState<ApplicationActionOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [selectedActionId, setSelectedActionId] = useState<string | null>(null);
  const [messageApi, contextHolder] = message.useMessage();

  const refresh = useCallback(async () => {
    if (!applicationId) return;
    setLoading(true);
    try {
      const data = await listApplicationActions(applicationId);
      setActions(data.items);
      setSelectedActionId((prev) => prev ?? data.items[0]?.id ?? null);
    } catch (err) {
      messageApi.warning(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [applicationId, messageApi]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selected =
    actions.find((a) => a.id === selectedActionId) ?? actions[0] ?? null;

  const handleApprove = async (actionId: string) => {
    try {
      setSubmitting(true);
      const updated = await approveApplicationAction(applicationId, actionId);
      setActions((prev) =>
        prev.map((a) => (a.id === actionId ? updated : a)),
      );
      messageApi.success("已批准");
    } catch (err) {
      messageApi.error(apiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleRevoke = async (actionId: string) => {
    try {
      setSubmitting(true);
      const updated = await revokeApplicationAction(applicationId, actionId);
      setActions((prev) =>
        prev.map((a) => (a.id === actionId ? updated : a)),
      );
      messageApi.success("已撤销");
    } catch (err) {
      messageApi.error(apiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card title="外部动作审批" size="small">
      {contextHolder}
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message="审批边界：不执行任何外部操作"
          description="在此预览、批准或撤销计划的外部动作。系统不会自动投递或发送消息——未来平台工具必须在执行前通过审批边界校验。"
        />

        <Space wrap>
          <Button onClick={() => setPreviewOpen(true)}>新建动作预览</Button>
          {actions.length > 0 && (
            <Select
              style={{ minWidth: 360 }}
              value={selected?.id}
              onChange={setSelectedActionId}
              options={actions.map((a, idx) => ({
                label: `#${actions.length - idx} · ${ACTION_TYPE_LABEL[a.action_type]} · ${STATUS_LABEL[a.status]} · ${formatTime(a.created_at)}`,
                value: a.id,
              }))}
            />
          )}
        </Space>

        {loading && actions.length === 0 ? (
          <Spin />
        ) : actions.length === 0 ? (
          <Empty description="暂无计划动作，点击「新建动作预览」创建" />
        ) : selected ? (
          <ActionDetail
            action={selected}
            onApprove={handleApprove}
            onRevoke={handleRevoke}
            submitting={submitting}
          />
        ) : null}
      </Space>

      <PreviewModal
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
        applicationId={applicationId}
        sourceHash={sourceHash}
        resumeVersionId={resumeVersionId}
        jobId={jobId}
        onCreated={(created) => {
          setActions((prev) => [created, ...prev]);
          setSelectedActionId(created.id);
          setPreviewOpen(false);
          messageApi.success("动作预览已创建");
        }}
      />
    </Card>
  );
}

/**
 * Detailed view of a single planned action: shows the exact payload preview,
 * source snapshot, approval state, and approve/revoke buttons gated by status.
 */
function ActionDetail({
  action,
  onApprove,
  onRevoke,
  submitting,
}: {
  action: ApplicationActionOut;
  onApprove: (id: string) => void;
  onRevoke: (id: string) => void;
  submitting: boolean;
}) {
  const { payload_preview, source_snapshot, approval } = action;
  const canApprove =
    action.status === "approval_required" || action.status === "stale";
  const canRevoke =
    action.status === "approved" ||
    action.status === "approval_required" ||
    action.status === "stale";

  return (
    <Space direction="vertical" size="small" style={{ width: "100%" }}>
      <Descriptions column={2} bordered size="small">
        <Descriptions.Item label="动作类型">
          <Tag>{ACTION_TYPE_LABEL[action.action_type]}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={STATUS_COLOR[action.status]}>
            {STATUS_LABEL[action.status]}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="Action ID" span={2}>
          <Text code>{action.id}</Text>
        </Descriptions.Item>
      </Descriptions>

      <Card type="inner" title="载荷预览（即将执行的内容）" size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="目标平台">
            {payload_preview.target_platform ?? "-"}
          </Descriptions.Item>
          <Descriptions.Item label="目标资源">
            {payload_preview.target_resource ?? "-"}
          </Descriptions.Item>
          <Descriptions.Item label="选中的产物 ID">
            {payload_preview.selected_artifact_ids.length > 0
              ? payload_preview.selected_artifact_ids.join(", ")
              : "-"}
          </Descriptions.Item>
          <Descriptions.Item label="简历文件引用">
            {payload_preview.resume_file_reference ?? "-"}
          </Descriptions.Item>
        </Descriptions>
        {payload_preview.outgoing_text ? (
          <div style={{ marginTop: 8 }}>
            <Text strong>外发文本</Text>
            <Paragraph
              style={{
                whiteSpace: "pre-wrap",
                background: "#fafafa",
                padding: 8,
                borderRadius: 4,
                marginTop: 4,
              }}
            >
              {payload_preview.outgoing_text}
            </Paragraph>
          </div>
        ) : null}
        <Text type="secondary">
          载荷哈希：<Text code>{action.payload_hash}</Text>
        </Text>
      </Card>

      <Card type="inner" title="来源快照" size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="职位 ID">
            {source_snapshot.job_id}
          </Descriptions.Item>
          <Descriptions.Item label="简历版本 ID">
            {source_snapshot.resume_version_id ?? "-"}
          </Descriptions.Item>
          <Descriptions.Item label="产物 ID">
            {source_snapshot.artifact_ids.length > 0
              ? source_snapshot.artifact_ids.join(", ")
              : "-"}
          </Descriptions.Item>
          <Descriptions.Item label="来源哈希">
            <Text code>{source_snapshot.source_hash}</Text>
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {approval ? (
        <Card type="inner" title="审批记录" size="small">
          <Descriptions column={1} size="small">
            <Descriptions.Item label="批准人">
              <Text code>{approval.approved_by}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="批准时间">
              {formatTime(approval.approved_at)}
            </Descriptions.Item>
            <Descriptions.Item label="批准的载荷哈希">
              <Text code>{approval.approved_payload_hash}</Text>
            </Descriptions.Item>
          </Descriptions>
        </Card>
      ) : null}

      {action.stale_reason ? (
        <Alert
          type="warning"
          showIcon
          message="动作已过期"
          description={action.stale_reason}
        />
      ) : null}

      <Space>
        <Button
          type="primary"
          disabled={!canApprove}
          loading={submitting}
          onClick={() => onApprove(action.id)}
        >
          批准
        </Button>
        <Popconfirm
          title="确认撤销此动作的审批？"
          description="撤销后需要重新预览并批准才能执行。"
          onConfirm={() => onRevoke(action.id)}
          disabled={!canRevoke}
        >
          <Button danger disabled={!canRevoke || submitting}>
            撤销
          </Button>
        </Popconfirm>
      </Space>
    </Space>
  );
}

/**
 * Modal form for creating a new action preview. Submits to the preview
 * endpoint, which stores the action in ``approval_required`` status — no
 * external execution is triggered.
 */
function PreviewModal({
  open,
  onClose,
  applicationId,
  sourceHash,
  resumeVersionId,
  jobId,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  applicationId: string;
  sourceHash: string | null;
  resumeVersionId: string | null;
  jobId: string;
  onCreated: (action: ApplicationActionOut) => void;
}) {
  const [actionType, setActionType] = useState<ExternalActionType>(
    "platform_submit",
  );
  const [targetPlatform, setTargetPlatform] = useState("");
  const [targetResource, setTargetResource] = useState("");
  const [outgoingText, setOutgoingText] = useState("");
  const [resumeFileRef, setResumeFileRef] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const handleSubmit = async () => {
    if (!sourceHash) {
      messageApi.error("来源哈希缺失，无法创建动作预览");
      return;
    }
    const payload: ApplicationActionCreate = {
      action_type: actionType,
      target_platform: targetPlatform.trim() || null,
      target_resource: targetResource.trim() || null,
      selected_artifact_ids: [],
      outgoing_text: outgoingText.trim() || null,
      resume_file_reference: resumeFileRef.trim() || null,
      source_snapshot: {
        job_id: jobId,
        resume_version_id: resumeVersionId,
        artifact_ids: [],
        source_hash: sourceHash,
      },
    };
    try {
      setSubmitting(true);
      const created = await previewApplicationAction(applicationId, payload);
      onCreated(created);
      // Reset form fields.
      setTargetPlatform("");
      setTargetResource("");
      setOutgoingText("");
      setResumeFileRef("");
    } catch (err) {
      messageApi.error(apiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="新建动作预览"
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      okText="创建预览"
      cancelText="取消"
      confirmLoading={submitting}
      width={560}
    >
      {contextHolder}
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Alert
          type="warning"
          showIcon
          message="预览不会执行任何外部操作"
          description="创建后动作状态为「待审批」。批准后，未来平台工具需通过审批边界校验才会执行。"
        />
        <Descriptions column={1} size="small">
          <Descriptions.Item label="动作类型">
            <Select
              style={{ width: 200 }}
              value={actionType}
              onChange={(v) => setActionType(v as ExternalActionType)}
              options={(
                Object.keys(ACTION_TYPE_LABEL) as ExternalActionType[]
              ).map((t) => ({
                label: ACTION_TYPE_LABEL[t],
                value: t,
              }))}
            />
          </Descriptions.Item>
          <Descriptions.Item label="目标平台">
            <input
              style={{ width: "100%", padding: "4px 8px" }}
              placeholder="如 boss, lagou"
              value={targetPlatform}
              onChange={(e) => setTargetPlatform(e.target.value)}
            />
          </Descriptions.Item>
          <Descriptions.Item label="目标资源">
            <input
              style={{ width: "100%", padding: "4px 8px" }}
              placeholder="如职位链接或 HR 会话 ID"
              value={targetResource}
              onChange={(e) => setTargetResource(e.target.value)}
            />
          </Descriptions.Item>
          <Descriptions.Item label="简历文件引用">
            <input
              style={{ width: "100%", padding: "4px 8px" }}
              placeholder="如要上传的简历版本 ID"
              value={resumeFileRef}
              onChange={(e) => setResumeFileRef(e.target.value)}
            />
          </Descriptions.Item>
        </Descriptions>
        <div>
          <Text strong>外发文本</Text>
          <textarea
            style={{
              width: "100%",
              minHeight: 80,
              padding: "8px",
              marginTop: 4,
              borderRadius: 4,
              border: "1px solid #d9d9d9",
            }}
            placeholder="即将发送的完整文本内容（用户需看到并批准确切内容）"
            value={outgoingText}
            onChange={(e) => setOutgoingText(e.target.value)}
          />
        </div>
      </Space>
    </Modal>
  );
}
