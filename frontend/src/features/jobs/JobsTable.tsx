import { useState, useEffect, useRef } from "react";
import { ProTable, type ActionType, type ProColumns } from "@ant-design/pro-components";
import { Button, Tag, message } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import type { AgentRunOut, JobOut } from "@/types";
import {
  apiErrorMessage,
  listAgentRuns,
  listJobs,
} from "@/api/client";
import { JobCreateModal } from "@/features/jobs/JobCreateModal";
import { useNavigate } from "react-router-dom";
import {
  ACTIVE_AGENT_RUN_STATUSES,
  type AgentRunStatus,
} from "@/features/agent-runs/status";

/** Placeholder company/title the backend writes while a parse is in flight. */
const PARSE_INFLIGHT_PLACEHOLDER = "(解析中…)";

/** Polling interval for refreshing the list while a parse run is active. */
const PARSE_POLL_INTERVAL_MS = 3000;

/**
 * Determine the parse status tag for a job row.
 *
 * - Jobs whose company/title are still the placeholder have a parse in flight
 *   (or a parse that failed before overwriting the placeholder). We look up
 *   the latest ``jd_paste_parsing`` run for the job to decide the tag.
 * - Jobs with a populated ``jd_normalized`` are fully parsed.
 * - Everything else (manual entry, or a parse that overwrote the placeholder)
 *   shows "已录入".
 */
function useParseStatus(jobs: JobOut[]): {
  statusByJobId: Record<string, "parsing" | "queued" | "failed" | "done">;
  hasActiveParse: boolean;
} {
  const [runByJobId, setRunByJobId] = useState<
    Record<string, AgentRunOut | undefined>
  >({});

  // Collect job IDs that still show the placeholder (parse potentially active).
  const inflightJobIds = jobs
    .filter(
      (j) =>
        j.company === PARSE_INFLIGHT_PLACEHOLDER ||
        j.title === PARSE_INFLIGHT_PLACEHOLDER,
    )
    .map((j) => j.id);

  // Fetch the latest jd_paste_parsing run for each inflight job. This is a
  // best-effort lookup: if it fails we just show "解析中".
  useEffect(() => {
    if (inflightJobIds.length === 0) {
      setRunByJobId({});
      return;
    }
    let cancelled = false;
    (async () => {
      const entries: [string, AgentRunOut | undefined][] = [];
      for (const jobId of inflightJobIds) {
        try {
          const res = await listAgentRuns(1, 5, jobId, "jd_paste_parsing");
          const latest = res.items[0];
          entries.push([jobId, latest]);
        } catch {
          entries.push([jobId, undefined]);
        }
      }
      if (!cancelled) {
        setRunByJobId(Object.fromEntries(entries));
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inflightJobIds.join(",")]);

  const statusByJobId: Record<string, "parsing" | "queued" | "failed" | "done"> =
    {};
  let hasActiveParse = false;
  for (const job of jobs) {
    const isPlaceholder =
      job.company === PARSE_INFLIGHT_PLACEHOLDER ||
      job.title === PARSE_INFLIGHT_PLACEHOLDER;
    if (!isPlaceholder) {
      statusByJobId[job.id] = "done";
      continue;
    }
    const run = runByJobId[job.id];
    if (run && ACTIVE_AGENT_RUN_STATUSES.has(run.status as AgentRunStatus)) {
      statusByJobId[job.id] = run.status === "running" ? "parsing" : "queued";
      hasActiveParse = true;
    } else if (run && run.status === "failed") {
      statusByJobId[job.id] = "failed";
    } else {
      // No run found yet (still being created) or run succeeded without
      // overwriting the placeholder (edge case). Show "解析中" optimistically.
      statusByJobId[job.id] = "queued";
      hasActiveParse = true;
    }
  }
  return { statusByJobId, hasActiveParse };
}

export function JobsTable() {
  const [createOpen, setCreateOpen] = useState(false);
  const actionRef = useRef<ActionType>();
  const navigate = useNavigate();

  // We need the current page of jobs to drive the parse-status lookup. ProTable
  // owns its data; we mirror it here via the request callback so the hook can
  // inspect it.
  const [currentJobs, setCurrentJobs] = useState<JobOut[]>([]);
  const { statusByJobId, hasActiveParse } = useParseStatus(currentJobs);

  // Auto-refresh the list while any parse run is active. We re-fire the
  // ProTable request on an interval; when all parses reach a terminal state
  // (hasActiveParse becomes false) the interval clears.
  useEffect(() => {
    if (!hasActiveParse) return;
    const timer = setInterval(() => {
      actionRef.current?.reload();
    }, PARSE_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [hasActiveParse]);

  const columns: ProColumns<JobOut>[] = [
    { title: "平台", dataIndex: "platform", width: 90 },
    {
      title: "公司",
      dataIndex: "company",
      width: 160,
      render: (_, record) => {
        const st = statusByJobId[record.id];
        if (st === "parsing" || st === "queued") {
          return <Tag color="processing">{record.company}</Tag>;
        }
        if (st === "failed") {
          return <Tag color="error">{record.company}</Tag>;
        }
        return record.company;
      },
    },
    { title: "职位", dataIndex: "title", width: 180 },
    { title: "城市", dataIndex: "location", width: 100 },
    { title: "薪资", dataIndex: "salary_range", width: 120 },
    { title: "方向", dataIndex: "direction", width: 100 },
    {
      title: "解析状态",
      dataIndex: "jd_normalized",
      width: 100,
      search: false,
      render: (_, record) => {
        const st = statusByJobId[record.id];
        if (st === "parsing") return <Tag color="processing">解析中</Tag>;
        if (st === "queued") return <Tag color="default">排队中</Tag>;
        if (st === "failed") return <Tag color="error">解析失败</Tag>;
        if (record.jd_normalized) return <Tag color="success">已解析</Tag>;
        return <Tag>已录入</Tag>;
      },
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
        <a key="detail" onClick={() => navigate(`/jobs/${record.id}`)}>
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
            setCurrentJobs(data.items);
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
          message.success("JD 已创建，正在解析…");
          actionRef.current?.reload();
        }}
      />
    </>
  );
}
