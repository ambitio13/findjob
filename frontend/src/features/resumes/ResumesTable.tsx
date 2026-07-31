import { useEffect, useRef, useState } from "react";
import { ProTable, type ActionType, type ProColumns } from "@ant-design/pro-components";
import { Button, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import type { ResumeOut } from "@/types";
import { apiErrorMessage, listResumes } from "@/api/client";
import { ResumeUploadModal } from "@/features/resumes/ResumeUploadModal";

interface Props {
  reloadToken?: number;
}

export function ResumesTable({ reloadToken = 0 }: Props) {
  const [uploadOpen, setUploadOpen] = useState(false);
  const actionRef = useRef<ActionType>();
  const navigate = useNavigate();

  useEffect(() => {
    if (reloadToken > 0) actionRef.current?.reload();
  }, [reloadToken]);

  const columns: ProColumns<ResumeOut>[] = [
    { title: "文件名", dataIndex: "filename", ellipsis: true },
    { title: "类型", dataIndex: "mime_type", width: 140, search: false },
    {
      title: "最新版本",
      dataIndex: "latest_version_no",
      width: 90,
      search: false,
      render: (_, record) =>
        record.latest_version_no ? `v${record.latest_version_no}` : "-",
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 160,
      valueType: "dateTime",
      search: false,
    },
    {
      title: "操作",
      valueType: "option",
      width: 80,
      render: (_text, record) => [
        <a key="detail" onClick={() => navigate(`/resumes/${record.id}`)}>
          详情
        </a>,
      ],
    },
  ];

  return (
    <>
      <ProTable<ResumeOut>
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        search={false}
        pagination={{ defaultPageSize: 20 }}
        request={async (params) => {
          try {
            const page = params.current ?? 1;
            const pageSize = params.pageSize ?? 20;
            const data = await listResumes(page, pageSize);
            return {
              data: data.items,
              total: data.meta.total,
              success: true,
            };
          } catch (err) {
            message.error(apiErrorMessage(err));
            return { data: [], total: 0, success: false };
          }
        }}
        toolBarRender={() => [
          <Button
            key="upload"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setUploadOpen(true)}
          >
            上传简历
          </Button>,
        ]}
        headerTitle="简历列表"
      />
      <ResumeUploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={() => {
          setUploadOpen(false);
          actionRef.current?.reload();
        }}
      />
    </>
  );
}
