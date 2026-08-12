import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Descriptions,
  Input,
  Progress,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  apiErrorMessage,
  getBatchLoopStatus,
  listJobs,
  pauseBatchLoop,
  resumeBatchLoop,
  startBatchLoop,
} from "@/api/client";
import { useBridgeStatus } from "./useBridgeStatus";
import type {
  BatchItemStatus,
  BatchLoopItemOut,
  BatchLoopStatusOut,
  BatchRunStatus,
  JobOut,
} from "@/types";

const { Text } = Typography;

/** Status labels for a batch run (mirrors ``BatchRunStatus``). */
const RUN_STATUS_LABEL: Record<BatchRunStatus, string> = {
  running: "进行中",
  paused: "已暂停",
  completed: "已完成",
  hard_stopped: "已硬停止",
  failed: "已失败",
};

const RUN_STATUS_COLOR: Record<BatchRunStatus, string> = {
  running: "processing",
  paused: "default",
  completed: "success",
  hard_stopped: "error",
  failed: "error",
};

/** Status labels for a batch item (mirrors ``BatchItemStatus``). */
const ITEM_STATUS_LABEL: Record<BatchItemStatus, string> = {
  pending: "待处理",
  inspecting: "处理中",
  prepared: "已准备",
  needs_review: "需人工审阅",
  skipped: "已跳过",
  failed: "失败",
  stopped: "已停止",
};

const ITEM_STATUS_COLOR: Record<BatchItemStatus, string> = {
  pending: "default",
  inspecting: "processing",
  prepared: "green",
  needs_review: "orange",
  skipped: "default",
  failed: "red",
  stopped: "default",
};

/** Decision labels (reused from pilot panel). */
const DECISION_LABEL: Record<string, string> = {
  communicate: "建议沟通",
  skip: "跳过",
  needs_review: "需人工审阅",
};

/**
 * BatchModePanel — the GUI for the BOSS recommended-job batch loop.
 *
 * Serially processes the recommended-jobs list: inspect → match → prepare per
 * job, stopping at ``approval_required`` or ``needs_review``. The batch never
 * auto-approves or auto-executes in ``prepare_only`` mode — every prepared
 * action must still be individually approved via the Pilot panel.
 *
 * Safety invariants (evolution-contracts.md §1):
 * - The batch loop only automates read-only / side-effect-free steps.
 * - Execute always requires explicit human approval; the batch never touches
 *   the userscript bridge for execution.
 * - Hard-stop after 3 consecutive failures protects against cascading errors.
 */
export function BatchModePanel() {
  // --- job list state -------------------------------------------------------
  const [jobs, setJobs] = useState<JobOut[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [selectedJobIds, setSelectedJobIds] = useState<string[]>([]);

  // --- batch run state ------------------------------------------------------
  const [batchStatus, setBatchStatus] = useState<BatchLoopStatusOut | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --- resume selection (reuse listJobs to find jobs, but need resume version) ---
  const [resumeVersionId, setResumeVersionId] = useState<string>("");
  const [limit, setLimit] = useState(10);

  const [messageApi, contextHolder] = message.useMessage();

  // Bridge status — batch inspect needs the userscript connected.
  const { status: bridgeStatus } = useBridgeStatus(true);
  const bridgeConnected = bridgeStatus?.connected ?? false;

  // Polling ref for batch status.
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // --- load jobs ------------------------------------------------------------
  const loadJobs = useCallback(async () => {
    setJobsLoading(true);
    try {
      // Fetch BOSS platform jobs only. We fetch a large page to cover the
      // typical recommended-jobs list; the batch limit caps actual processing.
      const data = await listJobs(1, 100);
      const bossJobs = data.items.filter((j) => j.platform === "boss");
      setJobs(bossJobs);
    } catch (err) {
      messageApi.warning(apiErrorMessage(err));
    } finally {
      setJobsLoading(false);
    }
  }, [messageApi]);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  // --- batch status polling -------------------------------------------------
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const pollStatus = useCallback(
    (runId: string) => {
      stopPolling();
      pollRef.current = setTimeout(async () => {
        try {
          const status = await getBatchLoopStatus(runId);
          setBatchStatus(status);
          // Continue polling if the run is still active.
          if (
            status.status === "running" ||
            status.status === "paused"
          ) {
            pollStatus(runId);
          }
        } catch {
          // Non-fatal: polling will retry on next tick.
        }
      }, 3000);
    },
    [stopPolling],
  );

  useEffect(() => {
    return () => stopPolling();
  }, [stopPolling]);

  // --- actions --------------------------------------------------------------
  const handleStart = useCallback(async () => {
    if (selectedJobIds.length === 0) {
      messageApi.warning("请至少选择一个职位");
      return;
    }
    if (!resumeVersionId) {
      messageApi.warning("请输入简历版本 ID");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const status = await startBatchLoop({
        resume_version_id: resumeVersionId,
        job_ids: selectedJobIds,
        limit,
        mode: "prepare_only",
      });
      setBatchStatus(status);
      messageApi.success("批量处理已启动");
      // Start polling if the run is still active.
      if (status.status === "running") {
        pollStatus(status.run_id);
      }
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [selectedJobIds, resumeVersionId, limit, messageApi, pollStatus]);

  const handlePause = useCallback(async () => {
    if (!batchStatus) return;
    setBusy(true);
    setError(null);
    try {
      const result = await pauseBatchLoop(batchStatus.run_id);
      messageApi.info(result.message);
      // Immediately refresh status.
      const status = await getBatchLoopStatus(batchStatus.run_id);
      setBatchStatus(status);
      stopPolling();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [batchStatus, messageApi, stopPolling]);

  const handleResume = useCallback(async () => {
    if (!batchStatus) return;
    setBusy(true);
    setError(null);
    try {
      const status = await resumeBatchLoop(batchStatus.run_id);
      setBatchStatus(status);
      messageApi.success("批量处理已恢复");
      if (status.status === "running") {
        pollStatus(status.run_id);
      }
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [batchStatus, messageApi, pollStatus]);

  // --- derived state --------------------------------------------------------
  const isRunning = batchStatus?.status === "running";
  const isPaused = batchStatus?.status === "paused";
  const isTerminal =
    batchStatus != null &&
    batchStatus.status !== "running" &&
    batchStatus.status !== "paused";

  const progressPercent = useMemo(() => {
    if (!batchStatus || batchStatus.total === 0) return 0;
    return Math.round((batchStatus.processed / batchStatus.total) * 100);
  }, [batchStatus]);

  // --- table columns --------------------------------------------------------
  const columns = useMemo(
    () => [
      {
        title: "职位",
        key: "title",
        render: (_: unknown, _record: BatchLoopItemOut, index: number) => {
          const job = jobs.find((j) => j.id === _record.job_id);
          return job
            ? `${job.company} · ${job.title}`
            : `职位 #${index + 1}`;
        },
      },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        render: (status: BatchItemStatus) => (
          <Tag color={ITEM_STATUS_COLOR[status]}>
            {ITEM_STATUS_LABEL[status]}
          </Tag>
        ),
      },
      {
        title: "决策",
        dataIndex: "decision",
        key: "decision",
        render: (decision: string | null) =>
          decision ? (
            <Tag>{DECISION_LABEL[decision] ?? decision}</Tag>
          ) : (
            <Text type="secondary">-</Text>
          ),
      },
      {
        title: "评分",
        dataIndex: "score",
        key: "score",
        render: (score: number | null) =>
          score != null ? (
            <Text>{score.toFixed(2)}</Text>
          ) : (
            <Text type="secondary">-</Text>
          ),
      },
      {
        title: "错误",
        dataIndex: "error",
        key: "error",
        render: (error: string | null) =>
          error ? (
            <Text type="danger" style={{ fontSize: 12 }}>
              {error}
            </Text>
          ) : null,
      },
    ],
    [jobs],
  );

  // --- job selection table --------------------------------------------------
  const jobColumns = useMemo(
    () => [
      {
        title: "职位",
        key: "title",
        render: (_: unknown, record: JobOut) =>
          `${record.company} · ${record.title}`,
      },
      {
        title: "城市",
        dataIndex: "location",
        key: "location",
        render: (v: string | null) => v ?? "-",
      },
    ],
    [],
  );

  const rowSelection = {
    selectedRowKeys: selectedJobIds,
    onChange: (keys: React.Key[]) => {
      setSelectedJobIds(keys as string[]);
    },
  };

  return (
    <Card
      type="inner"
      title="BOSS 推荐列表批量处理"
      size="small"
    >
      {contextHolder}
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {/* Safety notice */}
        <Alert
          type="info"
          showIcon
          message="批量半自动模式：自动执行「读取→匹配→准备」，停在人工审批前"
          description="批量循环串行处理每个职位，每个准备好的沟通动作仍需在 Pilot 面板中逐个审批后才能执行发送。"
        />

        {/* Bridge status warning */}
        {!bridgeConnected ? (
          <Alert
            type="warning"
            showIcon
            message="userscript 未连接"
            description="批量处理需要 userscript 连接以读取职位详情。请先安装 userscript 并打开 BOSS 职位页面。"
          />
        ) : null}

        {/* Config form */}
        {!batchStatus || isTerminal ? (
          <Space direction="vertical" size="small" style={{ width: "100%" }}>
            <Descriptions column={1} size="small">
              <Descriptions.Item label="简历版本 ID">
                <Input
                  placeholder="输入简历版本 ID"
                  value={resumeVersionId}
                  onChange={(e) => setResumeVersionId(e.target.value)}
                  style={{ maxWidth: 400 }}
                />
              </Descriptions.Item>
              <Descriptions.Item label="批量上限">
                <Input
                  type="number"
                  min={1}
                  max={50}
                  value={limit}
                  onChange={(e) =>
                    setLimit(Math.max(1, Number(e.target.value) || 10))
                  }
                  style={{ maxWidth: 120 }}
                />
              </Descriptions.Item>
            </Descriptions>

            <Text strong>选择职位（{selectedJobIds.length} 已选）</Text>
            <Table
              size="small"
              rowKey="id"
              columns={jobColumns}
              dataSource={jobs}
              loading={jobsLoading}
              rowSelection={rowSelection}
              pagination={{ pageSize: 10, size: "small" }}
            />

            <Space wrap>
              <Button
                type="primary"
                loading={busy}
                disabled={
                  busy ||
                  selectedJobIds.length === 0 ||
                  !resumeVersionId ||
                  !bridgeConnected
                }
                onClick={handleStart}
              >
                启动批量处理
              </Button>
              <Button onClick={() => setSelectedJobIds([])} disabled={busy}>
                清空选择
              </Button>
              <Checkbox
                checked={selectedJobIds.length === jobs.length && jobs.length > 0}
                onChange={(e) => {
                  if (e.target.checked) {
                    setSelectedJobIds(jobs.map((j) => j.id));
                  } else {
                    setSelectedJobIds([]);
                  }
                }}
              >
                全选
              </Checkbox>
            </Space>
          </Space>
        ) : null}

        {/* Batch run status */}
        {batchStatus ? (
          <Space direction="vertical" size="small" style={{ width: "100%" }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="批次状态">
                <Tag color={RUN_STATUS_COLOR[batchStatus.status]}>
                  {RUN_STATUS_LABEL[batchStatus.status]}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="进度">
                {batchStatus.processed} / {batchStatus.total}
              </Descriptions.Item>
              <Descriptions.Item label="连续失败">
                <Text
                  type={
                    batchStatus.consecutive_failures > 0 ? "danger" : "secondary"
                  }
                >
                  {batchStatus.consecutive_failures} / {batchStatus.hard_stop_threshold}
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="批次 ID">
                <Text code>{batchStatus.run_id}</Text>
              </Descriptions.Item>
            </Descriptions>

            <Progress percent={progressPercent} size="small" />

            {batchStatus.message ? (
              <Alert
                type={
                  batchStatus.status === "hard_stopped" || batchStatus.status === "failed"
                    ? "error"
                    : "info"
                }
                showIcon
                message={batchStatus.message}
              />
            ) : null}

            {/* Pause/Resume controls */}
            <Space wrap>
              {isRunning ? (
                <Button loading={busy} onClick={handlePause}>
                  暂停
                </Button>
              ) : null}
              {isPaused ? (
                <Button
                  type="primary"
                  loading={busy}
                  onClick={handleResume}
                >
                  恢复
                </Button>
              ) : null}
            </Space>

            {/* Per-item results table */}
            <Table
              size="small"
              rowKey="job_id"
              columns={columns}
              dataSource={batchStatus.items}
              pagination={false}
            />
          </Space>
        ) : null}

        {error ? (
          <Alert type="error" showIcon message="操作失败" description={error} />
        ) : null}
      </Space>
    </Card>
  );
}
