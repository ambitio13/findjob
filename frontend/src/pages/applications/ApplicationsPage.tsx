import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Alert,
  Card,
  Descriptions,
  Empty,
  message,
  Select,
  Space,
  Spin,
  Tag,
  Timeline,
  Typography,
} from "antd";
import {
  apiErrorMessage,
  getApplication,
  getJob,
  listApplications,
} from "@/api/client";
import { ApplicationActionsPanel } from "@/features/applications/ApplicationActionsPanel";
import type { ApplicationOut, JobOut } from "@/types";

const { Paragraph, Text } = Typography;

const APP_STATUS_COLOR: Record<string, string> = {
  planned: "default",
  preparing: "blue",
  ready: "cyan",
  submitted: "green",
  interviewing: "purple",
  offered: "gold",
  rejected: "red",
  failed: "volcano",
};

const APP_STATUS_LABEL: Record<string, string> = {
  planned: "计划中",
  preparing: "准备中",
  ready: "就绪",
  submitted: "已投递",
  interviewing: "面试中",
  offered: "已 offer",
  rejected: "已拒绝",
  failed: "失败",
};

const TIMELINE_COLOR: Record<string, string> = {
  created: "blue",
  status_changed: "blue",
  user_note: "gray",
  action_previewed: "orange",
  action_approved: "green",
  action_revoked: "red",
  action_stale: "volcano",
};

function formatTime(ts: string | null): string {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

/**
 * Application records center: lists the user's application records and, when
 * one is selected, shows its timeline and the external-action approval panel.
 */
export function ApplicationsPage() {
  const [applications, setApplications] = useState<ApplicationOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messageApi, contextHolder] = message.useMessage();

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listApplications(1, 50);
      setApplications(data.items);
      setSelectedId((prev) => prev ?? data.items[0]?.id ?? null);
    } catch (err) {
      messageApi.warning(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [messageApi]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const selected =
    applications.find((a) => a.id === selectedId) ?? applications[0] ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {contextHolder}
      <Card title="投递记录">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {loading && applications.length === 0 ? (
            <Spin />
          ) : applications.length === 0 ? (
            <Empty
              description={
                <span>
                  暂无投递记录。请先在{" "}
                  <Link to="/jobs">职位</Link> 详情页创建投递记录。
                </span>
              }
            />
          ) : (
            <Select
              style={{ minWidth: 400 }}
              value={selected?.id}
              onChange={setSelectedId}
              options={applications.map((a, idx) => ({
                label: `#${applications.length - idx} · ${APP_STATUS_LABEL[a.status] ?? a.status} · ${formatTime(a.created_at)}`,
                value: a.id,
              }))}
            />
          )}
          {selected ? <ApplicationDetail applicationId={selected.id} /> : null}
        </Space>
      </Card>
    </div>
  );
}

/** Detailed view for a single application: job info, timeline, actions panel. */
function ApplicationDetail({ applicationId }: { applicationId: string }) {
  const [app, setApp] = useState<ApplicationOut | null>(null);
  const [job, setJob] = useState<JobOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await getApplication(applicationId);
        if (!active) return;
        setApp(data);
        try {
          const jobData = await getJob(data.job_id);
          if (active) setJob(jobData);
        } catch {
          // Job may have been deleted; leave as null.
        }
      } catch (err) {
        if (active) setError(apiErrorMessage(err));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [applicationId]);

  if (loading && !app) return <Spin />;
  if (error || !app) {
    return (
      <Alert type="error" message="加载失败" description={error ?? undefined} />
    );
  }

  // Extract the source hash from readiness_snapshot if available. The panel
  // uses this as the source_hash for new action previews so staleness
  // detection works against the current readiness source.
  const sourceHash =
    typeof app.readiness_snapshot === "object" &&
    app.readiness_snapshot !== null &&
    typeof app.readiness_snapshot.source_hash === "string"
      ? app.readiness_snapshot.source_hash
      : null;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card type="inner" title="投递信息" size="small">
        <Descriptions column={2} size="small">
          <Descriptions.Item label="状态">
            <Tag color={APP_STATUS_COLOR[app.status] ?? "default"}>
              {APP_STATUS_LABEL[app.status] ?? app.status}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="Application ID">
            <Text code>{app.id}</Text>
          </Descriptions.Item>
          {job ? (
            <>
              <Descriptions.Item label="公司">{job.company}</Descriptions.Item>
              <Descriptions.Item label="职位">{job.title}</Descriptions.Item>
              <Descriptions.Item label="平台">{job.platform}</Descriptions.Item>
              <Descriptions.Item label="城市">
                {job.location ?? "-"}
              </Descriptions.Item>
            </>
          ) : (
            <Descriptions.Item label="职位 ID">
              <Text code>{app.job_id}</Text>
            </Descriptions.Item>
          )}
          <Descriptions.Item label="简历版本 ID">
            {app.resume_version_id ?? "-"}
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {formatTime(app.created_at)}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card type="inner" title="时间线" size="small">
        {app.timeline.length > 0 ? (
          <Timeline
            items={app.timeline.map((e) => ({
              color: TIMELINE_COLOR[e.type] ?? "gray",
              children: (
                <Space direction="vertical" size={0}>
                  <Space>
                    <Tag>{e.type}</Tag>
                    <Text type="secondary">{formatTime(e.at)}</Text>
                  </Space>
                  {e.summary ? <Text>{e.summary}</Text> : null}
                  {e.from_status || e.to_status ? (
                    <Text type="secondary">
                      {e.from_status ?? "-"} → {e.to_status ?? "-"}
                    </Text>
                  ) : null}
                </Space>
              ),
            }))}
          />
        ) : (
          <Empty description="暂无时间线事件" />
        )}
      </Card>

      {app.latest_error ? (
        <Alert
          type="error"
          message="最近错误"
          description={
            <Paragraph style={{ marginBottom: 0 }}>
              <pre style={{ margin: 0, fontSize: 12 }}>
                {JSON.stringify(app.latest_error, null, 2)}
              </pre>
            </Paragraph>
          }
        />
      ) : null}

      <ApplicationActionsPanel
        applicationId={app.id}
        sourceHash={sourceHash}
        resumeVersionId={app.resume_version_id}
        jobId={app.job_id}
        key={app.id}
      />
    </Space>
  );
}
