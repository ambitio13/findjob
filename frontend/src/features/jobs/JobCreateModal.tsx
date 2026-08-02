import { useState } from "react";
import { ModalForm, ProFormText, ProFormTextArea } from "@ant-design/pro-components";
import { Alert, Button, Space, message } from "antd";
import { apiErrorMessage, createJob, parseJobJd } from "@/api/client";
import type { JobCreate } from "@/types";
import { asyncRunProgressMessage, runIdHint } from "@/features/agent-runs/copy";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

const WORKFLOW_LABEL = "JD 解析";

/**
 * Paste-first job creation modal (create-job-first async flow):
 *
 *  **智能解析**: paste raw JD → click "智能解析" → the endpoint creates a
 *  ``JobPosting`` row up front (company/title placeholder) plus a ``queued``
 *  AgentRun, enqueues the parse job, and returns immediately with HTTP 202.
 *  The modal closes right away so the user sees the new job in the list with a
 *  "解析中" status; the worker writes the parsed draft back into
 *  ``job.jd_normalized`` and overwrites the placeholder company/title on
 *  success. The list polls and refreshes naturally (see ``JobsTable``).
 *
 *  **跳过解析手动填写**: a manual-entry two-field form (company/title required
 *  + JD text) that creates the job synchronously via ``POST /jobs``.
 */
export function JobCreateModal({ open, onClose, onCreated }: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [manualMode, setManualMode] = useState(false);
  const [rawJd, setRawJd] = useState("");
  const [messageApi, contextHolder] = message.useMessage();

  const handleParse = async () => {
    if (!rawJd.trim()) {
      messageApi.warning("请先粘贴 JD 原文");
      return;
    }
    setParsing(true);
    try {
      const res = await parseJobJd(rawJd);
      // The backend created the job up front and enqueued the parse. Close the
      // modal immediately — the job list will show the new row with a "解析中"
      // status and refresh as the worker writes back the parsed draft.
      messageApi.success(
        `${asyncRunProgressMessage(WORKFLOW_LABEL)}，职位已加入列表，${runIdHint(res.run.id)}`,
      );
      onCreated();
      // Reset local state for the next open.
      setRawJd("");
      setParsing(false);
    } catch (err) {
      setParsing(false);
      messageApi.error(apiErrorMessage(err));
    }
  };

  const handleReset = () => {
    setParsing(false);
    setManualMode(false);
    setRawJd("");
  };

  const handleSkipParse = () => {
    setManualMode(true);
  };

  return (
    <ModalForm<JobCreate>
      title={manualMode ? "手动录入 JD" : "粘贴 JD 智能解析"}
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
      submitter={
        manualMode
          ? undefined
          : {
              // In parse mode the primary action is the "智能解析" button below;
              // hide the default submitter so only manual-entry uses onFinish.
              submitButtonProps: { style: { display: "none" } },
              resetButtonProps: { style: { display: "none" } },
            }
      }
      onFinish={async (values) => {
        // Manual-entry save path: create the job synchronously with the typed
        // fields + JD text. jd_normalized stays null (no parse).
        setSubmitting(true);
        try {
          await createJob({
            ...values,
            jd_raw: rawJd,
            platform: values.platform ?? "manual",
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
      {!manualMode ? (
        <>
          <ProFormTextArea
            name="jd_raw_input"
            label="JD 原文"
            placeholder="粘贴完整 JD 原文，点击「智能解析」后会自动创建职位并在列表中异步填充字段"
            rules={[{ required: true, message: "请粘贴 JD 原文" }]}
            fieldProps={{
              autoSize: { minRows: 6, maxRows: 16 },
              onChange: (e) => setRawJd(e.target.value),
            }}
          />
          <Space>
            <Button type="primary" loading={parsing} onClick={handleParse}>
              {parsing ? "提交中…" : "智能解析"}
            </Button>
            <Button onClick={handleSkipParse}>跳过解析，手动填写</Button>
          </Space>
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 12 }}
            message="点击「智能解析」后会立即创建职位并关闭弹窗，解析在后台进行，完成后自动填充到列表。"
          />
        </>
      ) : (
        <>
          <ProFormText
            name="company"
            label="公司"
            rules={[{ required: true, message: "请输入公司名称" }]}
          />
          <ProFormText
            name="title"
            label="职位"
            rules={[{ required: true, message: "请输入职位名称" }]}
          />
          <ProFormText name="location" label="城市" />
          <ProFormText name="salary_range" label="薪资范围" />
          <ProFormText name="direction" label="方向" />
          <ProFormText name="platform" label="平台" />
          <ProFormTextArea
            name="jd_raw_input"
            label="JD 原文"
            placeholder="粘贴或输入 JD 原文"
            rules={[{ required: true, message: "请输入 JD 原文" }]}
            fieldProps={{
              autoSize: { minRows: 4, maxRows: 12 },
              onChange: (e) => setRawJd(e.target.value),
            }}
          />
        </>
      )}
    </ModalForm>
  );
}
