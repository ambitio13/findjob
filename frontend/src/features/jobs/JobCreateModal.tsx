import { useState } from "react";
import {
  ModalForm,
  ProFormText,
  ProFormTextArea,
} from "@ant-design/pro-components";
import { Alert, Button, Input, Space, Typography, message } from "antd";
import { apiErrorMessage, createJob, parseJobJd } from "@/api/client";
import type {
  JdNormalized,
  JdNormalizedExtraction,
  JdParseDraftFields,
  JdParseRunSummary,
  JobCreate,
} from "@/types";

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

/**
 * Two-phase paste-first job creation modal:
 *  1. Paste raw JD → click "智能解析" → model parses structured draft fields.
 *  2. Edit the pre-filled fields (company/title required) → save creates the
 *     JobPosting with the parsed draft persisted in ``jd_normalized``.
 *
 * Parse failure is recoverable: a warning is shown with a link to the run
 * detail, and the user proceeds to manual entry with empty fields.
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

  const handleParse = async () => {
    if (!rawJd.trim()) {
      message.warning("请先粘贴 JD 原文");
      return;
    }
    setParsing(true);
    try {
      const res = await parseJobJd(rawJd);
      setRunSummary(res.run);
      setExtraction(res.extraction);
      if (res.status === "succeeded") {
        setFields(res.fields);
        setParsed(true);
        message.success("解析完成，请确认并补充字段");
      } else {
        // Recoverable failure: keep the (empty) fields so the user can fall
        // back to manual entry, and surface the failed run for inspection.
        setFields(EMPTY_FIELDS);
        setParsed(true);
        message.warning("解析未成功，可手动填写字段");
      }
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setParsing(false);
    }
  };

  const handleReset = () => {
    setParsed(false);
    setFields(EMPTY_FIELDS);
    setExtraction(null);
    setRunSummary(null);
    setRawJd("");
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
          message.error(apiErrorMessage(err));
          return false;
        } finally {
          setSubmitting(false);
        }
      }}
    >
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
              智能解析
            </Button>
            <Button
              onClick={() => {
                setParsed(true);
                setExtraction(null);
                setRunSummary(null);
              }}
            >
              跳过解析，手动填写
            </Button>
          </Space>
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
                  （可前往「Agent 运行」查看轨迹），请手动填写字段后保存。
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
