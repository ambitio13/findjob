import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  message,
  Modal,
  Popconfirm,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Timeline,
  Typography,
} from "antd";
import {
  apiErrorMessage,
  appendTimelineNote,
  getApplication,
  getJob,
  getResume,
  inspectCurrentJob,
  listApplications,
  listResumes,
  updateApplicationStatus,
} from "@/api/client";
import { ApplicationActionsPanel } from "@/features/applications/ApplicationActionsPanel";
import { ArtifactChecklist } from "@/features/applications/ArtifactChecklist";
import { FailurePanel } from "@/features/applications/FailurePanel";
import { FollowUpSuggestionsPanel } from "@/features/applications/FollowUpSuggestionsPanel";
import { GuidedSubmitPanel } from "@/features/applications/GuidedSubmitPanel";
import { ManualSubmitPanel } from "@/features/applications/ManualSubmitPanel";
import { OutcomePanel } from "@/features/applications/OutcomePanel";
import { RecommendedJobPilotPanel } from "@/features/applications/RecommendedJobPilotPanel";
import { BatchModePanel } from "@/features/applications/BatchModePanel";
import { RecommendedDiscoveryPanel } from "@/features/applications/RecommendedDiscoveryPanel";
import { ReadinessSummary } from "@/features/applications/ReadinessSummary";
import { SourceSnapshotPanel } from "@/features/applications/SourceSnapshotPanel";
import { useBridgeStatus } from "@/features/applications/useBridgeStatus";
import {
  APP_STATUS_COLOR,
  APP_STATUS_LABEL,
  TIMELINE_COLOR,
  canTransition,
  normalizeApplicationStatus,
  type ApplicationStatus,
} from "@/features/applications/status";
import type {
  ApplicationFailureEnvelope,
  ApplicationFailureNextAction,
  ApplicationOut,
  ApplicationTimelineEventOut,
  JobOut,
  ReadinessArtifactOut,
  ResumeOut,
} from "@/types";

const { Text } = Typography;

function formatTime(ts: string | null): string {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

/**
 * Independent BOSS inspect entry point. Provides a way to trigger
 * ``inspectCurrentJob`` without requiring an existing application record,
 * breaking the chicken-and-egg dependency between PilotPanel and the
 * application list.
 *
 * When the userscript bridge is connected and a resume is selected, the user
 * clicks "读取当前职位" to read the JD on the current BOSS job page. On
 * success the parent receives the new application id via ``onInspected``.
 */
function BossInspectEntry({
  onInspected,
}: {
  onInspected: (applicationId: string) => void;
}) {
  const { status: bridgeStatus, loading: bridgeLoading } =
    useBridgeStatus(true);
  const [resumes, setResumes] = useState<ResumeOut[]>([]);
  // Map resume id → latest version id (needed for inspect's resume_version_id).
  const [resumeVersionMap, setResumeVersionMap] = useState<
    Record<string, string>
  >({});
  const [selectedResumeId, setSelectedResumeId] = useState<string | null>(null);
  const [resumeLoading, setResumeLoading] = useState(false);
  const [inspecting, setInspecting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setResumeLoading(true);
    listResumes(1, 50)
      .then(async (list) => {
        if (cancelled) return;
        const details = await Promise.all(
          list.items.map((r) => getResume(r.id)),
        );
        if (cancelled) return;
        setResumes(list.items);
        const versionMap: Record<string, string> = {};
        for (const d of details) {
          if (d.latest_version) {
            versionMap[d.id] = d.latest_version.id;
          }
        }
        setResumeVersionMap(versionMap);
        // Auto-select if there is exactly one resume with a latest version.
        if (list.items.length === 1) {
          setSelectedResumeId(list.items[0].id);
        }
      })
      .catch(() => {
        // Resume fetch failure is non-fatal; user can upload via /resumes.
      })
      .finally(() => {
        if (!cancelled) setResumeLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const connected = bridgeStatus?.connected ?? false;
  const resumeVersionId = selectedResumeId
    ? resumeVersionMap[selectedResumeId] ?? null
    : null;

  const handleInspect = async () => {
    if (!resumeVersionId) {
      void message.warning("请先选择一份简历");
      return;
    }
    setInspecting(true);
    try {
      const result = await inspectCurrentJob(resumeVersionId);
      if (result.inspect_status === "ok" && result.application) {
        void message.success("已读取当前职位并创建投递记录");
        onInspected(result.application.id);
      } else if (result.inspect_status === "jd_too_sparse" && result.application) {
        void message.warning(
          result.message ?? "职位描述过于简单，已创建记录但建议补充信息",
        );
        onInspected(result.application.id);
      } else {
        // read_failed — include the agent_run_id so the user can locate the
        // run in logs / Agent Runs detail.
        const runHint = result.agent_run_id
          ? `（追踪 ID：${result.agent_run_id}）`
          : "";
        void message.error(
          `${result.message ?? "读取职位失败，请确认 BOSS 页面已打开"}${runHint}`,
        );
      }
    } catch (err) {
      void message.error(apiErrorMessage(err));
    } finally {
      setInspecting(false);
    }
  };

  return (
    <Card title="BOSS 职位读取" size="small">
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        {/* Bridge status */}
        {bridgeLoading && !bridgeStatus ? (
          <Text type="secondary">正在检测 userscript 连接状态…</Text>
        ) : connected ? (
          <Alert
            type="success"
            showIcon
            message="userscript 已连接"
            description={
              bridgeStatus?.page_title
                ? `当前页面：${bridgeStatus.page_title}`
                : "已连接到 BOSS 页面"
            }
          />
        ) : (
          <Alert
            type="warning"
            showIcon
            message="userscript 未连接"
            description={
              <span>
                请先安装 userscript 并打开 BOSS 职位页面。安装说明见{" "}
                <Link to="/settings">设置</Link>。
              </span>
            }
          />
        )}

        {/* Resume selection */}
        {resumeLoading ? (
          <Spin size="small" />
        ) : resumes.length === 0 ? (
          <Text type="secondary">
            还没有简历。请先到 <Link to="/resumes">简历管理</Link> 上传简历。
          </Text>
        ) : (
          <Select
            style={{ minWidth: 320 }}
            placeholder="选择简历"
            value={selectedResumeId ?? undefined}
            onChange={setSelectedResumeId}
            options={resumes.map((r) => ({
              label: `${r.filename}${r.latest_version_no ? ` (v${r.latest_version_no})` : ""}`,
              value: r.id,
            }))}
          />
        )}

        {/* Inspect button */}
        <Button
          type="primary"
          loading={inspecting}
          disabled={!connected || !resumeVersionId}
          onClick={handleInspect}
        >
          读取当前职位
        </Button>
      </Space>
    </Card>
  );
}

/**
 * Application records center: lists the user's application records and, when
 * one is selected, shows the full readiness panel — summary, source snapshot,
 * artifact checklist, failure envelope, timeline, and the external-action
 * approval slot.
 *
 * The composition order mirrors ``design.md``:
 *   ReadinessSummary → SourceSnapshotPanel → ArtifactChecklist → FailurePanel
 *   → TimelinePanel → ApprovalPreviewSlot.
 */
export function ApplicationsPage() {
  const [applications, setApplications] = useState<ApplicationOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messageApi, contextHolder] = message.useMessage();
  const location = useLocation();

  // Router state passed from the create-application flow. The create endpoint
  // returns ``is_duplicate`` accurately; a later GET resets it to its default
  // (false), so we carry the fresh-create signal forward here instead of
  // relying on the application object loaded inside ``ApplicationDetail``.
  const freshState = useMemo(() => {
    const s = location.state as
      | { freshApplicationId?: string; freshIsDuplicate?: boolean }
      | null;
    return s && typeof s.freshApplicationId === "string"
      ? { id: s.freshApplicationId, isDuplicate: s.freshIsDuplicate ?? false }
      : null;
  }, [location.state]);

  const refresh = useCallback(
    async (forceSelectId?: string | null) => {
      setLoading(true);
      try {
        const data = await listApplications(1, 50);
        setApplications(data.items);
        if (forceSelectId) {
          setSelectedId(forceSelectId);
        } else {
          setSelectedId((prev) => prev ?? data.items[0]?.id ?? null);
        }
      } catch (err) {
        messageApi.warning(apiErrorMessage(err));
      } finally {
        setLoading(false);
      }
    },
    [messageApi],
  );

  const handleInspected = useCallback(
    (appId: string) => {
      void refresh(appId);
    },
    [refresh],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // After the list loads, auto-select the freshly-created application so the
  // user lands on its readiness panel and auto-generation can fire.
  useEffect(() => {
    if (freshState && applications.length > 0) {
      const exists = applications.some((a) => a.id === freshState.id);
      if (exists) setSelectedId(freshState.id);
    }
  }, [freshState, applications]);

  const selected =
    applications.find((a) => a.id === selectedId) ?? applications[0] ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {contextHolder}
      <BossInspectEntry onInspected={handleInspected} />
      <FollowUpSuggestionsPanel />
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
                label: `#${applications.length - idx} · ${APP_STATUS_LABEL[normalizeApplicationStatus(a.status)]} · ${formatTime(a.created_at)}`,
                value: a.id,
              }))}
            />
          )}
          {selected ? (
            <ApplicationDetail
              applicationId={selected.id}
              freshCreate={
                freshState && freshState.id === selected.id
                  ? !freshState.isDuplicate
                  : undefined
              }
            />
          ) : null}
        </Space>
      </Card>
    </div>
  );
}

/** Detailed view for a single application: the full readiness panel. */
function ApplicationDetail({
  applicationId,
  freshCreate,
}: {
  applicationId: string;
  /**
   * ``true`` when this detail was opened directly from a successful
   * (non-duplicate) create-application flow. The readiness panel uses this to
   * auto-generate artifacts *only* for genuinely new records, instead of
   * relying on ``ApplicationOut.is_duplicate`` which a later GET resets to its
   * default (false) — that would cause old planned records to spuriously
   * auto-generate on every visit. ``undefined`` means "no fresh-create signal"
   * (normal navigation/reload), in which case auto-generation never fires.
   */
  freshCreate?: boolean;
}) {
  const [app, setApp] = useState<ApplicationOut | null>(null);
  const [job, setJob] = useState<JobOut | null>(null);
  const [artifacts, setArtifacts] = useState<ReadinessArtifactOut[]>([]);
  const [generating, setGenerating] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState(false);
  const [noteOpen, setNoteOpen] = useState(false);
  // Active Tab. Reset to the default ("materials") whenever the user switches
  // application records so a stale Tab choice does not leak across records.
  const [activeTab, setActiveTab] = useState<string>("materials");
  const [messageApi, contextHolder] = message.useMessage();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getApplication(applicationId);
      setApp(data);
      try {
        const jobData = await getJob(data.job_id);
        setJob(jobData);
      } catch {
        setJob(null);
      }
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Reset the active Tab whenever the user switches to a different application
  // record — a stale Tab selection (e.g. the boss-only Pilot Tab) should not
  // carry over to a non-boss record.
  useEffect(() => {
    setActiveTab("materials");
  }, [applicationId]);

  const handleTerminal = useCallback(() => {
    void load();
  }, [load]);

  // Stale detection: compare the application snapshot's source_hash to the
  // latest artifact's source_hash. When they differ, artifacts are stale.
  const latestArtifact = artifacts[0] ?? null;
  const stale = useMemo(() => {
    if (!app || !latestArtifact) return false;
    const snapHash = readSourceHash(app.readiness_snapshot);
    const artHash =
      latestArtifact.source_ids &&
      typeof latestArtifact.source_ids.source_hash === "string"
        ? latestArtifact.source_ids.source_hash
        : null;
    return snapHash !== null && artHash !== null && snapHash !== artHash;
  }, [app, latestArtifact]);

  if (loading && !app) return <Spin />;
  if (error || !app) {
    return (
      <Alert type="error" message="加载失败" description={error ?? undefined} />
    );
  }

  const status = normalizeApplicationStatus(app.status);
  const resumeMissing = !app.resume_version_id;
  const sourceHash = readSourceHash(app.readiness_snapshot);

  // Failure envelope: backend stores it as latest_error JSON dict. Readiness
  // failures may keep the application in its current status, so show the panel
  // whenever a latest_error exists.
  const failure = app.latest_error
    ? parseFailureEnvelope(app.latest_error)
    : null;

  const handleStatusTransition = async (target: ApplicationStatus, note?: string) => {
    try {
      setActing(true);
      const updated = await updateApplicationStatus(app.id, {
        status: target,
        note: note ?? null,
      });
      setApp(updated);
      messageApi.success(`状态已更新为「${APP_STATUS_LABEL[target]}」`);
    } catch (err) {
      messageApi.error(apiErrorMessage(err));
    } finally {
      setActing(false);
    }
  };

  const handleRetryFromFailure = (next: ApplicationFailureNextAction) => {
    // The most common next-action is retry → re-enter preparing so the user
    // can regenerate artifacts. Other actions map to manual guidance.
    if (next === "retry") {
      void handleStatusTransition("preparing");
    } else if (next === "choose_resume") {
      messageApi.info("请在职位详情页重新绑定简历版本");
    } else if (next === "edit_source") {
      messageApi.info("请更新简历或职位信息后重新生成");
    } else if (next === "reapprove") {
      messageApi.info("请在下方外部动作审批区重新审批");
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {contextHolder}

      {/* Public header — visible on every Tab. Kept compact so the Tabs row
          stays reachable without scrolling past the full metadata block. */}
      {/* Job + application metadata */}
      <Card type="inner" title="投递信息" size="small">
        <Descriptions column={2} size="small">
          <Descriptions.Item label="状态">
            <Tag color={APP_STATUS_COLOR[status]}>{APP_STATUS_LABEL[status]}</Tag>
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
            {app.resume_version_id ?? (
              <Text type="warning">未绑定</Text>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {formatTime(app.created_at)}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* Readiness summary — cross-workflow overview (both manual and Pilot
          need to see readiness at a glance). */}
      <ReadinessSummary
        application={app}
        generating={generating}
        artifactCount={artifacts.length}
        stale={stale}
      />

      {/* Failure panel — failures must always be visible, never hidden inside
          a Tab. The backend keeps a failed application in its current status,
          so this shows whenever a latest_error envelope exists. */}
      {failure ? (
        <FailurePanel
          failure={failure}
          onNextAction={handleRetryFromFailure}
          acting={acting}
        />
      ) : null}

      {/* Tabs split the two workflows + the boss-only Pilot. AntD Tabs default
          behavior is lazy first-render + keeping already-mounted panes alive
          on switch, so ArtifactChecklist's agent-run polling survives Tab
          switches (AC1). We set destroyInactiveTabPane={false} explicitly to
          document the invariant. */}
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        destroyInactiveTabPane={false}
        items={[
          {
            key: "materials",
            label: "材料准备",
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <SourceSnapshotPanel
                  application={app}
                  latestArtifact={latestArtifact}
                  stale={stale}
                />
                <ArtifactChecklist
                  application={app}
                  autoGenerate={freshCreate === true}
                  onTerminal={() => {
                    handleTerminal();
                    // The checklist manages its own artifact list; we refresh
                    // application detail to pick up the updated
                    // readiness_snapshot / timeline.
                  }}
                  resumeMissing={resumeMissing}
                  onStateChange={({ artifacts: arts, generating: gen }) => {
                    setArtifacts(arts);
                    setGenerating(gen);
                  }}
                />
                {/* Generate-and-copy path: platform-agnostic delivery — copy
                    the opening message / targeted resume and paste by hand.
                    Never depends on the userscript bridge. */}
                <ManualSubmitPanel artifacts={artifacts} />
              </Space>
            ),
          },
          {
            key: "approval",
            label: "审批与执行",
            children: (
              <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                <GuidedSubmitPanel application={app} onAfterChange={load} />
                {/* Status actions: pause / resume / mark submitted / manual */}
                <Card type="inner" title="状态操作" size="small">
                  <Space wrap>
                    {canTransition(status, "preparing") && status !== "preparing" ? (
                      <Popconfirm
                        title="开始准备？"
                        description="进入准备中状态后可生成就绪材料。"
                        onConfirm={() => handleStatusTransition("preparing")}
                        disabled={acting || resumeMissing}
                      >
                        <Button disabled={acting || resumeMissing}>开始准备</Button>
                      </Popconfirm>
                    ) : null}
                    {canTransition(status, "paused") ? (
                      <Popconfirm
                        title="暂停投递？"
                        description="暂停后可恢复到计划中或准备中。"
                        onConfirm={() => handleStatusTransition("paused")}
                        disabled={acting}
                      >
                        <Button disabled={acting}>暂停</Button>
                      </Popconfirm>
                    ) : null}
                    {canTransition(status, "planned") ? (
                      <Popconfirm
                        title="恢复为计划中？"
                        onConfirm={() => handleStatusTransition("planned")}
                        disabled={acting}
                      >
                        <Button disabled={acting}>恢复计划</Button>
                      </Popconfirm>
                    ) : null}
                    {canTransition(status, "preparing") && status === "paused" ? (
                      <Popconfirm
                        title="恢复准备中？"
                        onConfirm={() => handleStatusTransition("preparing")}
                        disabled={acting || resumeMissing}
                      >
                        <Button disabled={acting || resumeMissing}>恢复准备</Button>
                      </Popconfirm>
                    ) : null}
                    {canTransition(status, "submitted") ? (
                      <Popconfirm
                        title="标记为已投递？"
                        description="手动标记，不代表平台已验证投递结果。"
                        onConfirm={() => handleStatusTransition("submitted", "手动标记已投递")}
                        disabled={acting}
                      >
                        <Button type="primary" disabled={acting}>
                          标记已投递
                        </Button>
                      </Popconfirm>
                    ) : null}
                    {canTransition(status, "interviewing") ? (
                      <Popconfirm
                        title="标记为面试中？"
                        onConfirm={() => handleStatusTransition("interviewing", "进入面试")}
                        disabled={acting}
                      >
                        <Button disabled={acting}>面试中</Button>
                      </Popconfirm>
                    ) : null}
                    {canTransition(status, "rejected") ? (
                      <Popconfirm
                        title="标记为已拒绝？"
                        onConfirm={() => handleStatusTransition("rejected", "未通过")}
                        disabled={acting}
                      >
                        <Button danger disabled={acting}>
                          已拒绝
                        </Button>
                      </Popconfirm>
                    ) : null}
                    <Button onClick={() => setNoteOpen(true)}>添加备注</Button>
                  </Space>
                  {resumeMissing && status === "planned" ? (
                    <Text type="secondary" style={{ display: "block", marginTop: 8 }}>
                      需先在职位详情页绑定简历版本，才能开始准备。
                    </Text>
                  ) : null}
                </Card>

                {/* Outcome marking: one-click 已回复/被拒/约面 */}
                <OutcomePanel applicationId={app.id} onAfterChange={load} />

                {/* Approval preview slot (existing component) */}
                <ApplicationActionsPanel
                  applicationId={app.id}
                  sourceHash={sourceHash}
                  resumeVersionId={app.resume_version_id}
                  jobId={app.job_id}
                  onActionChange={load}
                  key={app.id}
                />

                <TimelinePanel
                  timeline={app.timeline}
                  onAddNote={() => setNoteOpen(true)}
                />
              </Space>
            ),
          },
          ...(job?.platform === "boss"
            ? [
                {
                  key: "pilot",
                  label: "BOSS 自动沟通 Pilot",
                  children: (
                    <RecommendedJobPilotPanel
                      application={app}
                      onAfterChange={load}
                    />
                  ),
                },
                {
                  key: "batch",
                  label: "批量处理",
                  children: <BatchModePanel />,
                },
                {
                  key: "discovery",
                  label: "推荐发现",
                  children: <RecommendedDiscoveryPanel />,
                },
              ]
            : []),
        ]}
      />

      <NoteModal
        open={noteOpen}
        onClose={() => setNoteOpen(false)}
        applicationId={app.id}
        onSubmitted={(updated) => {
          setApp(updated);
          setNoteOpen(false);
          messageApi.success("备注已添加");
        }}
      />
    </Space>
  );
}

/** Timeline panel: renders the append-only event log. */
function TimelinePanel({
  timeline,
  onAddNote,
}: {
  timeline: ApplicationTimelineEventOut[];
  onAddNote: () => void;
}) {
  return (
    <Card
      type="inner"
      title="时间线"
      size="small"
      extra={<Button size="small" onClick={onAddNote}>添加备注</Button>}
    >
      {timeline.length > 0 ? (
        <Timeline
          items={timeline.map((e) => ({
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
  );
}

/** Modal for appending a manual user note to the timeline. */
function NoteModal({
  open,
  onClose,
  applicationId,
  onSubmitted,
}: {
  open: boolean;
  onClose: () => void;
  applicationId: string;
  onSubmitted: (updated: ApplicationOut) => void;
}) {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const handleSubmit = async () => {
    if (!text.trim()) {
      messageApi.warning("请输入备注内容");
      return;
    }
    try {
      setSubmitting(true);
      const updated = await appendTimelineNote(applicationId, {
        summary: text.trim(),
      });
      onSubmitted(updated);
      setText("");
    } catch (err) {
      messageApi.error(apiErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="添加备注"
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      okText="添加"
      cancelText="取消"
      confirmLoading={submitting}
    >
      {contextHolder}
      <Input.TextArea
        rows={3}
        placeholder="记录投递过程中的备注信息"
        value={text}
        onChange={(e) => setText(e.target.value)}
        maxLength={500}
        showCount
      />
    </Modal>
  );
}

/** Read the source_hash from a readiness_snapshot JSON dict. */
function readSourceHash(
  snapshot: Record<string, unknown> | null,
): string | null {
  if (typeof snapshot === "object" && snapshot !== null) {
    const v = snapshot.source_hash;
    if (typeof v === "string") return v;
  }
  return null;
}

/** Best-effort parse of latest_error into a typed failure envelope. */
function parseFailureEnvelope(
  raw: Record<string, unknown>,
): ApplicationFailureEnvelope | null {
  if (typeof raw !== "object" || raw === null) return null;
  const category = raw.category;
  const code = raw.code;
  const msg = raw.message;
  if (typeof category !== "string" || typeof code !== "string" || typeof msg !== "string") {
    return null;
  }
  return {
    category: category as ApplicationFailureEnvelope["category"],
    code,
    message: msg,
    retryable: typeof raw.retryable === "boolean" ? raw.retryable : false,
    next_action:
      (typeof raw.next_action === "string"
        ? raw.next_action
        : "manual_review") as ApplicationFailureNextAction,
    agent_run_id:
      typeof raw.agent_run_id === "string" ? raw.agent_run_id : null,
    source_ids:
      typeof raw.source_ids === "object" && raw.source_ids !== null
        ? (raw.source_ids as Record<string, unknown>)
        : {},
    occurred_at:
      typeof raw.occurred_at === "string" ? raw.occurred_at : new Date().toISOString(),
  };
}
