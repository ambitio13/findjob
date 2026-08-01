import { useState } from "react";
import {
  ModalForm,
  ProFormText,
  ProFormTextArea,
} from "@ant-design/pro-components";
import { Alert, Button, Input, Space, message } from "antd";
import {
  apiErrorMessage,
  createJob,
  parseJobJd,
} from "@/api/client";
import type {
  JdNormalized,
  JdNormalizedExtraction,
  JdParseDraftFields,
  JdParseRunSummary,
  JdParseSubmitResponse,
  JobCreate,
} from "@/types";
import { TERMINAL_AGENT_RUN_STATUSES, type AgentRunStatus } from "@/features/agent-runs/status";
import { useAgentRunPolling } from "@/features/agent-runs/useAgentRunPolling";
import {
  asyncRunFailureMessage,
  asyncRunProgressMessage,
  asyncRunSuccessMessage,
  runIdHint,
} from "@/features/agent-runs/copy";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

/** Empty draft shape mirroring the backend's default JdPasteFactsModelOutput. */
const EMPTY_FIELDS: JdParseDraftFields = {
  title: null,
  company: null,
  platform: null,
  location: null,
  salary_range: null,
  direction: null,
  responsibilities: [],
  hard_requirements: [],
  nice_to_have_requirements: [],
  benefits_or_risk_clues: [],
  uncertain_fields: [],
};

const WORKFLOW_LABEL = "JD 解析";

/**
 * Safely coerce an unknown value (from the JSON ``result`` column) into the
 * typed draft-fields shape. If the shape is missing or malformed (e.g. an
 * older run without ``fields``), fall back to empty fields so the UI never
 * crashes on hydration.
 */
function hydrateFields(result: Record<string, unknown> | null): JdParseDraftFields {
  if (!result) return EMPTY_FIELDS;
  const raw = result.fields;
  if (typeof raw !== "object" || raw === null) return EMPTY_FIELDS;
  // Shallow-merge over the empty shape so missing keys get their defaults.
  return { ...EMPTY_FIELDS, ...(raw as Partial<JdParseDraftFields>) };
}

/**
 * Safely coerce the ``extraction`` block from ``AgentRun.result`` into the
 * typed provenance shape. Returns ``null`` on malformed/missing data so the
 * save path falls back to ``jd_normalized=null`` (manual entry).
 */
function hydrateExtraction(
  result: Record<string, unknown> | null,
): JdNormalizedExtraction | null {
  if (!result) return null;
  const raw = result.extraction;
  if (typeof raw !== "object" || raw === null) return null;
  return raw as JdNormalizedExtraction;
}

/**
 * Two-phase paste-first job creation modal (enqueue-and-poll):
 *  1. Paste raw JD → click "智能解析" → the endpoint enqueues a parse job and
 *     returns immediately with a ``queued`` AgentRun. The modal polls the run
 *     detail until terminal status, then hydrates the draft fields from
 *     ``AgentRun.result.fields`` on success.
 *  2. Edit the pre-filled fields (company/title required) → save creates the
 *     JobPosting with the parsed draft persisted in ``jd_normalized``.
 *
 * Parse failure is recoverable: a warning is shown with the run ID, and the
 * user proceeds to manual entry with empty fields. A retry button lets them
 * re-submit the same JD without retyping.
 */
export function JobCreateModal({ open, onClose, onCreated }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [parsed, setParsed] = useState(false);
  const [fields, setFields] = useState<JdParseDraftFields>(EMPTY_FIELDS);
  const [extraction, setExtraction] = useState<JdNormalizedExtraction | null>(
    null,
  );
  const [runSummary, setRunSummary] = useState<JdParseRunSummary | null>(null);
  const [pollRunId, setPollRunId] = useState<string | null>(null);
  const [rawJd, setRawJd] = useState("");
  const [messageApi, contextHolder] = message.useMessage();

  // Shared polling hook — handles recursive setTimeout, token-based
  // cancellation, and cleanup on unmount. The hook automatically stops when
  // the run reaches a terminal status.
  useAgentRunPolling(pollRunId, {
    onUpdate: (detail) => {
      setRunSummary({ id: detail.id, status: detail.status, error: detail.error });
    },
    onTerminal: (detail) => {
      setParsing(false);
      setPollRunId(null);
      if (detail.status === "succeeded") {
        setFields(hydrateFields(detail.result));
        setExtraction(hydrateExtraction(detail.result));
        setParsed(true);
        messageApi.success(asyncRunSuccessMessage(WORKFLOW_LABEL));
      } else {
        // Recoverable failure: keep empty fields so the user can fall
        // back to manual entry, and surface the failed run for inspection.
        setFields(EMPTY_FIELDS);
        setExtraction(null);
        setParsed(true);
        messageApi.warning(asyncRunFailureMessage(WORKFLOW_LABEL, detail.error));
      }
    },
  });

  const handleParse = async () => {
    if (!rawJd.trim()) {
      messageApi.warning("请先粘贴 JD 原文");
      return;
    }
    setPollRunId(null);
    setParsing(true);
    setRunSummary(null);
    setExtraction(null);
    setFields(EMPTY_FIELDS);
    try {
      const res: JdParseSubmitResponse = await parseJobJd(rawJd);
      setRunSummary(res.run);
      // If the enqueue itself failed (Redis down), the backend flips the run
      // to ``failed`` before returning — surface that immediately.
      if (TERMINAL_AGENT_RUN_STATUSES.has(res.run.status as AgentRunStatus)) {
        setParsing(false);
        if (res.run.status === "succeeded") {
          setFields(hydrateFields(null));
          setParsed(true);
          messageApi.success(asyncRunSuccessMessage(WORKFLOW_LABEL));
        } else {
          setFields(EMPTY_FIELDS);
          setParsed(true);
          messageApi.warning(asyncRunFailureMessage(WORKFLOW_LABEL, res.run.error));
        }
        return;
      }
      // The run is ``queued`` — start polling until terminal status.
      setPollRunId(res.run.id);
    } catch (err) {
      setParsing(false);
      messageApi.error(apiErrorMessage(err));
    }
  };

  const handleReset = () => {
    setPollRunId(null);
    setParsing(false);
    setParsed(false);
    setFields(EMPTY_FIELDS);
    setExtraction(null);
    setRunSummary(null);
    setRawJd("");
  };

  const handleSkipParse = () => {
    setPollRunId(null);
    setParsing(false);
    setParsed(true);
    setExtraction(null);
    setRunSummary(null);
    setFields(EMPTY_FIELDS);
  };

  return (
    <ModalForm<JobCreate>
      title="手动录入 JD"
      open={open}
      modalProps={{
        onCancel: () => {
          handleReset();
          onClose();
        },
        confirmLoading: submitting,
        destroyOnClose: true,
        width: 640,
      }}
      initialValues={{ platform: "manual" }}
      onFinish={async (values) => {
        setSubmitting(true);
        try {
          // Assemble the durable jd_normalized shape (design.md §"jd_normalized
          // shape"): ``fields`` is the model-parsed draft snapshot,
          // ``_extraction`` carries parse provenance. On manual entry (no
          // parse) jd_normalized stays null. The typed ``JdNormalized`` is
          // widened to the loose ``Record<string, unknown> | null`` expected by
          // the JobCreate API boundary.
          const jdNormalized: JdNormalized | null =
            parsed && extraction
              ? { _extraction: extraction, fields }
              : null;
          await createJob({
            ...values,
            jd_raw: rawJd,
            platform: values.platform ?? "manual",
            jd_normalized: jdNormalized as JobCreate["jd_normalized"],
          });
          onCreated();
          handleReset();
          return true;
        } catch (err) {
          messageApi.error(apiErrorMessage(err));
          return false;
        } finally {
          setSubmitting(false);
        }
      }}
    >
      {contextHolder}
      {!parsed ? (
        <>
          <ProFormTextArea
            name="jd_raw_input"
            label="JD 原文"
            placeholder="粘贴完整 JD 原文，点击「智能解析」自动提取字段"
            rules={[{ required: true, message: "请粘贴 JD 原文" }]}
            fieldProps={{
              autoSize: { minRows: 6, maxRows: 16 },
              onChange: (e) => setRawJd(e.target.value),
            }}
          />
          <Space>
            <Button type="primary" loading={parsing} onClick={handleParse}>
              {parsing ? "解析中…" : "智能解析"}
            </Button>
            <Button onClick={handleSkipParse}>跳过解析，手动填写</Button>
          </Space>
          {parsing && runSummary && (
            <Alert
              type="info"
              showIcon
              message={`${asyncRunProgressMessage(WORKFLOW_LABEL)}（${runIdHint(runSummary.id)}）`}
              style={{ marginTop: 12 }}
            />
          )}
        </>
      ) : (
        <>
          {runSummary && runSummary.status === "failed" && (
            <Alert
              type="warning"
              showIcon
              message={`${WORKFLOW_LABEL}未成功`}
              description={
                <span>
                  {runIdHint(runSummary.id)}（可前往「Agent 运行」查看轨迹），请手动填写字段后保存，或点击下方「重新解析」重试。
                </span>
              }
              style={{ marginBottom: 12 }}
            />
          )}
          <ProFormText
            name="company"
            label="公司"
            initialValue={fields.company ?? undefined}
            rules={[{ required: true, message: "请输入公司名称" }]}
          />
          <ProFormText
            name="title"
            label="职位"
            initialValue={fields.title ?? undefined}
            rules={[{ required: true, message: "请输入职位名称" }]}
          />
          <ProFormText
            name="location"
            label="城市"
            initialValue={fields.location ?? undefined}
          />
          <ProFormText
            name="salary_range"
            label="薪资范围"
            initialValue={fields.salary_range ?? undefined}
          />
          <ProFormText
            name="direction"
            label="方向"
            initialValue={fields.direction ?? undefined}
          />
          <ProFormText name="platform" label="平台" />

          {fields.responsibilities.length > 0 && (
            <FormListPreview title="职责" items={fields.responsibilities} />
          )}
          {fields.hard_requirements.length > 0 && (
            <FormListPreview title="硬性要求" items={fields.hard_requirements} />
          )}
          {fields.nice_to_have_requirements.length > 0 && (
            <FormListPreview
              title="加分项"
              items={fields.nice_to_have_requirements}
            />
          )}

          <div style={{ marginTop: 12 }}>
            <Space direction="vertical" size="small" style={{ width: "100%" }}>
              <Input.TextArea
                value={rawJd}
                onChange={(e) => setRawJd(e.target.value)}
                autoSize={{ minRows: 4, maxRows: 12 }}
                placeholder="JD 原文（可编辑）"
              />
              <Button type="link" onClick={handleReset} style={{ padding: 0 }}>
                ← 重新解析
              </Button>
            </Space>
          </div>
        </>
      )}
    </ModalForm>
  );
}

/** Read-only bullet preview of a parsed list field. */
function FormListPreview({ title, items }: { title: string; items: string[] }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <strong>{title}</strong>
      <ul style={{ margin: "4px 0", paddingLeft: 20 }}>
        {items.map((item, idx) => (
          <li key={idx}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
