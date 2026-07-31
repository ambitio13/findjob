import { useState } from "react";
import { Modal, Upload, type UploadProps, message } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import { apiErrorMessage, uploadResume } from "@/api/client";

const { Dragger } = Upload;

// Client-side mirror of the backend ACCEPTED_EXTENSIONS allowlist (lowercase,
// no dot). The backend is the source of truth; this is a UX pre-filter.
const ACCEPTED_EXTENSIONS = ["txt", "pdf", "docx", "doc", "rtf"];
// Client-side mirror of the backend resume_max_size_mb default. Kept loose so a
// future backend change does not silently desync the warning.
const MAX_SIZE_MB = 10;

interface Props {
  open: boolean;
  onClose: () => void;
  onUploaded: () => void;
}

interface DraggerProps {
  onUploaded: () => void;
}

export function ResumeUploadDragger({ onUploaded }: DraggerProps) {
  const [submitting, setSubmitting] = useState(false);

  const draggerProps: UploadProps = {
    multiple: false,
    maxCount: 1,
    // Prevent antd from auto-uploading; we drive the POST ourselves so we can
    // surface the backend's 413/422 error detail verbatim.
    beforeUpload: (file) => {
      const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
      if (!ACCEPTED_EXTENSIONS.includes(ext)) {
        message.error(`不支持的文件类型：.${ext || "未知"}（仅支持 txt/pdf/docx/doc/rtf）`);
        return Upload.LIST_IGNORE;
      }
      if (file.size > MAX_SIZE_MB * 1024 * 1024) {
        message.error(`文件超过 ${MAX_SIZE_MB} MB 限制`);
        return Upload.LIST_IGNORE;
      }
      // Kick off the upload manually.
      (async () => {
        setSubmitting(true);
        try {
          await uploadResume(file);
          message.success("简历已上传");
          onUploaded();
        } catch (err) {
          message.error(apiErrorMessage(err));
        } finally {
          setSubmitting(false);
        }
      })();
      // Return false to stop antd's own network call.
      return false;
    },
    showUploadList: false,
  };

  return (
    <Dragger {...draggerProps} disabled={submitting}>
      <p className="ant-upload-drag-icon">
        <InboxOutlined />
      </p>
      <p className="ant-upload-text">点击或拖拽文件到此区域上传</p>
      <p className="ant-upload-hint">
        支持 txt / pdf / docx / doc / rtf，单文件不超过 {MAX_SIZE_MB} MB。
        .doc 与 .rtf 暂不支持文本提取，仅留存原件。
      </p>
    </Dragger>
  );
}

export function ResumeUploadModal({ open, onClose, onUploaded }: Props) {
  return (
    <Modal
      title="上传简历"
      open={open}
      onCancel={onClose}
      footer={null}
      destroyOnClose
      confirmLoading={false}
    >
      <ResumeUploadDragger onUploaded={onUploaded} />
    </Modal>
  );
}
