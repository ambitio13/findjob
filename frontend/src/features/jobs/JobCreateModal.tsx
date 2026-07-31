import { useState } from "react";
import { ModalForm, ProFormText, ProFormTextArea } from "@ant-design/pro-components";
import { message } from "antd";
import { apiErrorMessage, createJob } from "@/api/client";
import type { JobCreate } from "@/types";

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

export function JobCreateModal({ open, onClose, onCreated }: Props) {
  const [submitting, setSubmitting] = useState(false);

  return (
    <ModalForm<JobCreate>
      title="手动录入 JD"
      open={open}
      modalProps={{ onCancel: onClose, confirmLoading: submitting, destroyOnClose: true }}
      onFinish={async (values) => {
        setSubmitting(true);
        try {
          await createJob({ ...values, platform: values.platform ?? "manual" });
          onCreated();
          return true;
        } catch (err) {
          message.error(apiErrorMessage(err));
          return false;
        } finally {
          setSubmitting(false);
        }
      }}
    >
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
      <ProFormTextArea
        name="jd_raw"
        label="JD 原文"
        rules={[{ required: true, message: "请粘贴 JD 原文" }]}
        fieldProps={{ autoSize: { minRows: 6, maxRows: 16 } }}
      />
    </ModalForm>
  );
}
