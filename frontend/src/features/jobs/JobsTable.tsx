import { useState } from "react";
import { ProTable, type ActionType, type ProColumns } from "@ant-design/pro-components";
import { Button, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { JobOut } from "@/types";
import { apiErrorMessage, listJobs } from "@/api/client";
import { JobCreateModal } from "@/features/jobs/JobCreateModal";
import { useNavigate } from "react-router-dom";
import { useRef } from "react";

export function JobsTable() {
  const [createOpen, setCreateOpen] = useState(false);
  const actionRef = useRef<ActionType>();
  const navigate = useNavigate();

  const columns: ProColumns<JobOut>[] = [
    { title: "平台", dataIndex: "platform", width: 90 },
    { title: "公司", dataIndex: "company", width: 160 },
    { title: "职位", dataIndex: "title", width: 180 },
    { title: "城市", dataIndex: "location", width: 100 },
    { title: "薪资", dataIndex: "salary_range", width: 120 },
    { title: "方向", dataIndex: "direction", width: 100 },
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
        <a
          key="detail"
          onClick={() => navigate(`/jobs/${record.id}`)}
        >
          详情
        </a>,
      ],
    },
  ];

  return (
    <>
      <ProTable<JobOut>
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        search={false}
        pagination={{ defaultPageSize: 20 }}
        request={async (params) => {
          try {
            const page = params.current ?? 1;
            const pageSize = params.pageSize ?? 20;
            const data = await listJobs(page, pageSize);
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
            key="create"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setCreateOpen(true)}
          >
            录入 JD
          </Button>,
        ]}
        headerTitle="职位列表"
      />
      <JobCreateModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false);
          message.success("JD 已创建");
          actionRef.current?.reload();
        }}
      />
    </>
  );
}
