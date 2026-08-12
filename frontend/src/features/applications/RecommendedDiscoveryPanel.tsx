import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Input,
  InputNumber,
  Progress,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  apiErrorMessage,
  getDiscoveryStatus,
  resumeDiscovery,
  startDiscovery,
} from "@/api/client";
import { useBridgeStatus } from "./useBridgeStatus";
import type {
  DiscoveryItemOut,
  DiscoveryItemStatus,
  DiscoveryRunStatus,
  DiscoveryStatusOut,
} from "@/types";

const { Text } = Typography;

/** Status labels for a discovery run (mirrors ``DiscoveryRunStatus``). */
const RUN_STATUS_LABEL: Record<DiscoveryRunStatus, string> = {
  running: "进行中",
  paused: "已暂停",
  completed: "已完成",
  hard_stopped: "已硬停止",
  failed: "已失败",
};

const RUN_STATUS_COLOR: Record<DiscoveryRunStatus, string> = {
  running: "processing",
  paused: "default",
  completed: "success",
  hard_stopped: "error",
  failed: "error",
};

/** Status labels for a discovery item (mirrors ``DiscoveryItemStatus``). */
const ITEM_STATUS_LABEL: Record<DiscoveryItemStatus, string> = {
  pending: "待处理",
  opening: "打开中",
  reading: "读取中",
  persisted: "已持久化",
  matching: "匹配中",
  prepared: "已准备",
  needs_review: "需人工审阅",
  skipped: "已跳过",
  failed: "失败",
  stopped: "已停止",
};

const ITEM_STATUS_COLOR: Record<DiscoveryItemStatus, string> = {
  pending: "default",
  opening: "processing",
  reading: "processing",
  persisted: "processing",
  matching: "processing",
  prepared: "green",
  needs_review: "orange",
  skipped: "default",
  failed: "red",
  stopped: "default",
};

/** Decision labels (reused from pilot/batch panels). */
const DECISION_LABEL: Record<string, string> = {
  communicate: "建议沟通",
  skip: "跳过",
  needs_review: "需人工审阅",
};

/**
 * RecommendedDiscoveryPanel — the GUI for the zero-navigation BOSS
 * recommended-job discovery pipeline.
 *
 * Unlike BatchModePanel (which requires pre-existing jobs to be selected),
 * discovery scans the BOSS recommended list page (/web/geek/jobs) in place:
 * scan → click card → read inline detail pane → upsert → match → prepare,
 * all serially. No page navigation occurs — clicking a card only switches the
 * inline detail pane.
 *
 * Safety invariants (evolution-contracts.md §1, §14):
 * - The pipeline never auto-approves or auto-executes in ``prepare_only`` mode.
 * - Hard-stop after 3 consecutive failures protects against cascading errors.
 * - ``needs_review`` / ``skipped`` (including ``already_persisted``) are item
 *   outcomes, not incidents — they reset the consecutive-failure counter.
 */
export function RecommendedDiscoveryPanel() {
  // --- run state ------------------------------------------------------------
  const [discoveryStatus, setDiscoveryStatus] =
    useState<DiscoveryStatusOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // --- config ---------------------------------------------------------------
  const [resumeVersionId, setResumeVersionId] = useState<string>("");
  const [limit, setLimit] = useState(3);

  const [messageApi, contextHolder] = message.useMessage();

  // Bridge status — discovery needs the userscript connected to scan the page.
  const { status: bridgeStatus } = useBridgeStatus(true);
  const bridgeConnected = bridgeStatus?.connected ?? false;

  // Polling ref for discovery status.
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // --- status polling -------------------------------------------------------
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
          const status = await getDiscoveryStatus(runId);
          setDiscoveryStatus(status);
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
    if (!resumeVersionId) {
      messageApi.warning("请输入简历版本 ID");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const status = await startDiscovery({
        resume_version_id: resumeVersionId,
        limit,
        mode: "prepare_only",
      });
      setDiscoveryStatus(status);
      messageApi.success("发现管道已启动");
      // Start polling if the run is still active.
      if (status.status === "running") {
        pollStatus(status.run_id);
      }
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [resumeVersionId, limit, messageApi, pollStatus]);

  const handleResume = useCallback(async () => {
    if (!discoveryStatus) return;
    setBusy(true);
    setError(null);
    try {
      const status = await resumeDiscovery(discoveryStatus.run_id);
      setDiscoveryStatus(status);
      messageApi.success("发现管道已恢复");
      if (status.status === "running") {
        pollStatus(status.run_id);
      }
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }, [discoveryStatus, messageApi, pollStatus]);

  // --- derived state --------------------------------------------------------
  const isPaused = discoveryStatus?.status === "paused";
  const isTerminal =
    discoveryStatus != null &&
    discoveryStatus.status !== "running" &&
    discoveryStatus.status !== "paused";

  const progressPercent = useMemo(() => {
    if (!discoveryStatus || discoveryStatus.total === 0) return 0;
    return Math.round(
      (discoveryStatus.processed / discoveryStatus.total) * 100,
    );
  }, [discoveryStatus]);

  // --- table columns --------------------------------------------------------
  const columns = useMemo(
    () => [
      {
        title: "#",
        dataIndex: "rank",
        key: "rank",
        width: 40,
      },
      {
        title: "职位",
        key: "title",
        render: (_: unknown, record: DiscoveryItemOut) =>
          record.title ? (
            record.company ? (
              `${record.company} · ${record.title}`
            ) : (
              record.title
            )
          ) : (
            <Text type="secondary">未知职位</Text>
          ),
      },
      {
        title: "状态",
        dataIndex: "status",
        key: "status",
        render: (status: DiscoveryItemStatus) => (
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
        title: "跳过原因",
        dataIndex: "skip_reason",
        key: "skip_reason",
        render: (skipReason: string | null) =>
          skipReason ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {skipReason === "already_persisted"
                ? "已有沟通记录"
                : skipReason}
            </Text>
          ) : null,
      },
      {
        title: "消息",
        dataIndex: "message",
        key: "message",
        render: (msg: string | null) =>
          msg ? (
            <Text type="danger" style={{ fontSize: 12 }}>
              {msg}
            </Text>
          ) : null,
      },
    ],
    [],
  );

  return (
    <Card
      type="inner"
      title="BOSS 推荐职位发现"
      size="small"
    >
      {contextHolder}
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {/* Safety notice */}
        <Alert
          type="info"
          showIcon
          message="零导航发现模式：自动扫描推荐列表 → 点击卡片 → 读取内嵌详情 → 匹配 → 准备"
          description="发现管道串行处理推荐列表上的职位卡片，自动完成读取与匹配，停在人工审批前。准备好的沟通动作仍需在 Pilot 面板中逐个审批。"
        />

        {/* Bridge status warning */}
        {!bridgeConnected ? (
          <Alert
            type="warning"
            showIcon
            message="userscript 未连接"
            description="发现管道需要 userscript 连接以扫描职位列表页面。请先安装 userscript 并打开 BOSS 推荐职位页面。"
          />
        ) : null}

        {/* Config form */}
        {!discoveryStatus || isTerminal ? (
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
              <Descriptions.Item label="扫描上限">
                <InputNumber
                  min={1}
                  max={10}
                  value={limit}
                  onChange={(v) => setLimit(v ?? 3)}
                  style={{ maxWidth: 120 }}
                />
                <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                  默认 3，最多 10
                </Text>
              </Descriptions.Item>
            </Descriptions>

            <Space wrap>
              <Button
                type="primary"
                loading={busy}
                disabled={
                  busy ||
                  !resumeVersionId ||
                  !bridgeConnected
                }
                onClick={handleStart}
              >
                启动发现
              </Button>
            </Space>
          </Space>
        ) : null}

        {/* Discovery run status */}
        {discoveryStatus ? (
          <Space direction="vertical" size="small" style={{ width: "100%" }}>
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="运行状态">
                <Tag color={RUN_STATUS_COLOR[discoveryStatus.status]}>
                  {RUN_STATUS_LABEL[discoveryStatus.status]}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="进度">
                {discoveryStatus.processed} / {discoveryStatus.total}
              </Descriptions.Item>
              <Descriptions.Item label="连续失败">
                <Text
                  type={
                    discoveryStatus.consecutive_failures > 0
                      ? "danger"
                      : "secondary"
                  }
                >
                  {discoveryStatus.consecutive_failures} /{" "}
                  {discoveryStatus.hard_stop_threshold}
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="运行 ID">
                <Text code>{discoveryStatus.run_id}</Text>
              </Descriptions.Item>
            </Descriptions>

            <Progress percent={progressPercent} size="small" />

            {discoveryStatus.message ? (
              <Alert
                type={
                  discoveryStatus.status === "hard_stopped" ||
                  discoveryStatus.status === "failed"
                    ? "error"
                    : "info"
                }
                showIcon
                message={discoveryStatus.message}
              />
            ) : null}

            {/* Resume is crash-recovery only in v0.1; pause is hidden for sync runs. */}
            <Space wrap>
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
              rowKey="job_key"
              columns={columns}
              dataSource={discoveryStatus.items}
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
