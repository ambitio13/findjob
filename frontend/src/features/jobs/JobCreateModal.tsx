import { useRef, useState } from "react";
import {
  ModalForm,
  ProFormText,
  ProFormTextArea,
} from "@ant-design/pro-components";
import { Alert, Button, Input, Space, Typography, message } from "antd";
import {
  apiErrorMessage,
  createJob,
  getAgentRunDetail,
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
import { TERMINAL_JD_PARSE_STATUSES } from "@/types";

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

/** Polling interval for non-terminal JD parse status (milliseconds). */
const JD_PARSE_POLL_MS = 3000;

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
  const [rawJd, setRawJd] = useState("");
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTokenRef = useRef(0);
  const [messageApi, contextHolder] = message.useMessage();

  const stopPolling = () => {
    pollTokenRef.current += 1;
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  };

  /**
   * Poll ``GET /agent-runs/{id}/detail`` until the run reaches a terminal
   * status (``succeeded`` or ``failed``). On success, hydrate the draft fields
   * and extraction from ``AgentRun.result``. On failure, surface a warning and
   * let the user fall back to manual entry or retry. Mirrors the recursive
   * ``setTimeout`` + ``active`` flag pattern in ``ResumeDetailPage``.
   */
  const pollRunDetail = (runId: string) => {
    const token = pollTokenRef.current;

    const poll = async () => {
      try {
        const detail = await getAgentRunDetail(runId);
        if (token !== pollTokenRef.current) return;
        setRunSummary({ id: detail.id, status: detail.status, error: detail.error });
        if (TERMINAL_JD_PARSE_STATUSES.has(detail.status)) {
          setParsing(false);
          if (detail.status === "succeeded") {
            setFields(hydrateFields(detail.result));
            setExtraction(hydrateExtraction(detail.result));
            setParsed(true);
            messageApi.success("解析完成，请确认并补充字段");
          } else {
            // Recoverable failure: keep empty fields so the user can fall
            // back to manual entry, and surface the failed run for inspection.
            setFields(EMPTY_FIELDS);
            setExtraction(null);
            setParsed(true);
            messageApi.warning("解析未成功，可手动填写字段或重试");
          }
          return;
        }
        pollTimerRef.current = setTimeout(poll, JD_PARSE_POLL_MS);
      } catch {
        // Network blips during polling are non-fatal; retry on next tick.
        if (token === pollTokenRef.current) {
          pollTimerRef.current = setTimeout(poll, JD_PARSE_POLL_MS);
        }
      }
    };

    pollTimerRef.current = setTimeout(poll, JD_PARSE_POLL_MS);
  };
  const handleParse = async () => {
    if (!rawJd.trim()) {
      messageApi.warning("请先粘贴 JD 原文");
      return;
    }
    stopPolling();
    setParsing(true);
    setRunSummary(null);
    setExtraction(null);
    setFields(EMPTY_FIELDS);
    try {
      const res: JdParseSubmitResponse = await parseJobJd(rawJd);
      setRunSummary(res.run);
      // If the enqueue itself failed (Redis down), the backend flips the run
      // to ``failed`` before returning — surface that immediately.
      if (TERMINAL_JD_PARSE_STATUSES.has(res.run.status)) {
        setParsing(false);
        if (res.run.status === "succeeded") {
          setFields(hydrateFields(null));
          setParsed(true);
          messageApi.success("解析完成，请确认并补充字段");
        } else {
          setFields(EMPTY_FIELDS);
          setParsed(true);
          messageApi.warning("解析未成功，可手动填写字段或重试");
        }
        return;
      }
      // The run is ``queued`` — start polling until terminal status.
      pollRunDetail(res.run.id);
    } catch (err) {
      setParsing(false);
      messageApi.error(apiErrorMessage(err));
    }
  };

  const handleReset = () => {
    stopPolling();
    setParsing(false);
    setParsed(false);
    setFields(EMPTY_FIELDS);
    setExtraction(null);
    setRunSummary(null);
    setRawJd("");
  };

  const handleSkipParse = () => {
    stopPolling();
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
              message={`已提交解析任务，正在轮询运行状态…（运行 ID: ${runSummary.id}）`}
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
              message="JD 解析未成功"
              description={
                <span>
                  运行 ID:{" "}
                  <Typography.Text code>{runSummary.id}</Typography.Text>
                  （可前往「Agent 运行」查看轨迹），请手动填写字段后保存，或点击下方「重新解析」重试。
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
