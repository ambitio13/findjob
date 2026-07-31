import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  List,
  message,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  apiErrorMessage,
  getJob,
  listResumes,
  listResumeVersions,
  runJdAnalysis,
} from "@/api/client";
import type {
  JobOut,
  ResumeOut,
  ResumeVersionListItem,
  RunJdAnalysisResponse,
} from "@/types";

const { Paragraph, Text } = Typography;

const SEVERITY_COLOR: Record<string, string> = {
  low: "green",
  medium: "orange",
  high: "red",
};

const RECOMMENDATION_LABEL: Record<string, string> = {
  strong_match: "高度匹配",
  possible_match: "可能匹配",
  weak_match: "匹配度较低",
  not_enough_info: "信息不足",
};

interface ResumeOption {
  resume: ResumeOut;
  versions: ResumeVersionListItem[];
}

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [resumes, setResumes] = useState<ResumeOut[]>([]);
  const [resumeOptions, setResumeOptions] = useState<ResumeOption[]>([]);
  const [resumesLoading, setResumesLoading] = useState(false);
  const [selectedResumeId, setSelectedResumeId] = useState<string | undefined>();
  const [selectedVersionId, setSelectedVersionId] = useState<
    string | undefined
  >();
  const [versionsLoading, setVersionsLoading] = useState(false);

  const [result, setResult] = useState<RunJdAnalysisResponse | null>(null);
  const [running, setRunning] = useState(false);

  // Load job detail.
  useEffect(() => {
    if (!id) return;
    let active = true;
    (async () => {
      try {
        const data = await getJob(id);
        if (active) setJob(data);
      } catch (err) {
        if (active) setError(apiErrorMessage(err));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [id]);

  // Load resumes for the current user.
  useEffect(() => {
    let active = true;
    (async () => {
      setResumesLoading(true);
      try {
        const data = await listResumes(1, 100);
        if (!active) return;
        setResumes(data.items);
      } catch (err) {
        if (active) message.warning(apiErrorMessage(err));
      } finally {
        if (active) setResumesLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // Load versions for all resumes once resumes arrive.
  useEffect(() => {
    let active = true;
    if (resumes.length === 0) {
      setResumeOptions([]);
      return;
    }
    (async () => {
      setVersionsLoading(true);
      try {
        const entries = await Promise.all(
          resumes.map(async (resume) => {
            let versions: ResumeVersionListItem[] = [];
            try {
              versions = await listResumeVersions(resume.id);
            } catch {
              versions = [];
            }
            return { resume, versions };
          }),
        );
        if (!active) return;
        setResumeOptions(entries);
        // Default-select the first resume's latest version.
        const first = entries[0];
        if (first && first.versions.length > 0) {
          setSelectedResumeId(first.resume.id);
          const latest = [...first.versions].sort(
            (a, b) => (b.version_no ?? 0) - (a.version_no ?? 0),
          )[0];
          setSelectedVersionId(latest.id);
        }
      } finally {
        if (active) setVersionsLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [resumes]);

  const versionsForSelected = useMemo(() => {
    const entry = resumeOptions.find((r) => r.resume.id === selectedResumeId);
    return entry?.versions ?? [];
  }, [resumeOptions, selectedResumeId]);

  if (loading) return <Spin />;
  if (error || !job) {
    return <Alert type="error" message="加载失败" description={error ?? undefined} />;
  }

  const noResumes = resumes.length === 0 && !resumesLoading;

  const handleRun = async () => {
    if (!id || !selectedVersionId) return;
    setRunning(true);
    try {
      const res = await runJdAnalysis(id, selectedVersionId);
      setResult(res);
      message.success("分析运行完成");
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card title="职位详情">
        <Descriptions column={2}>
          <Descriptions.Item label="平台">{job.platform}</Descriptions.Item>
          <Descriptions.Item label="公司">{job.company}</Descriptions.Item>
          <Descriptions.Item label="职位">{job.title}</Descriptions.Item>
          <Descriptions.Item label="城市">{job.location ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="薪资">{job.salary_range ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="方向">{job.direction ?? "-"}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="JD 原文">
        <Paragraph style={{ whiteSpace: "pre-wrap" }}>{job.jd_raw}</Paragraph>
      </Card>

      <Card title="JD 分析">
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {noResumes ? (
            <Alert
              type="info"
              showIcon
              message="尚未上传简历"
              description={
                <span>
                  请先前往{" "}
                  <Link to="/resumes">简历管理</Link> 上传简历后再运行分析。
                </span>
              }
            />
          ) : (
            <Space wrap>
              <Select
                style={{ minWidth: 240 }}
                placeholder="选择简历"
                loading={resumesLoading || versionsLoading}
                value={selectedResumeId}
                onChange={(value) => {
                  setSelectedResumeId(value);
                  const entry = resumeOptions.find(
                    (r) => r.resume.id === value,
                  );
                  if (entry && entry.versions.length > 0) {
                    const latest = [...entry.versions].sort(
                      (a, b) => (b.version_no ?? 0) - (a.version_no ?? 0),
                    )[0];
                    setSelectedVersionId(latest.id);
                  } else {
                    setSelectedVersionId(undefined);
                  }
                }}
                options={resumeOptions.map((r) => ({
                  label: r.resume.filename,
                  value: r.resume.id,
                }))}
              />
              <Select
                style={{ minWidth: 200 }}
                placeholder="选择简历版本"
                loading={versionsLoading}
                value={selectedVersionId}
                onChange={setSelectedVersionId}
                disabled={!selectedResumeId || versionsForSelected.length === 0}
                options={versionsForSelected.map((v) => ({
                  label: `v${v.version_no}`,
                  value: v.id,
                }))}
              />
              <Button
                type="primary"
                loading={running}
                disabled={!selectedVersionId}
                onClick={handleRun}
              >
                运行 JD 分析
              </Button>
            </Space>
          )}

          {result ? (
            <AnalysisResult result={result} />
          ) : (
            !noResumes && (
              <Empty description="暂未运行，选择简历版本后点击运行" />
            )
          )}
        </Space>
      </Card>
    </div>
  );
}

function AnalysisResult({ result }: { result: RunJdAnalysisResponse }) {
  const { agent_run, analysis, artifact, structured } = result;
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Descriptions column={2} bordered size="small">
        <Descriptions.Item label="Run ID">{agent_run.id}</Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={agent_run.status === "succeeded" ? "green" : "orange"}>
            {agent_run.status}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="匹配分">
          {structured.match_score ?? "-"}
        </Descriptions.Item>
        <Descriptions.Item label="风险分">
          {structured.risk_score ?? "-"}
        </Descriptions.Item>
        <Descriptions.Item label="推荐结论" span={2}>
          <Tag>
            {RECOMMENDATION_LABEL[structured.recommendation] ??
              structured.recommendation}
          </Tag>
        </Descriptions.Item>
      </Descriptions>

      <Card type="inner" title="岗位概述" size="small">
        <Paragraph>{structured.role_summary}</Paragraph>
        {structured.responsibilities.length > 0 && (
          <>
            <Text strong>职责</Text>
            <List
              size="small"
              dataSource={structured.responsibilities}
              renderItem={(item) => <List.Item>{item}</List.Item>}
            />
          </>
        )}
      </Card>

      <Card type="inner" title="要求" size="small">
        <Text strong>硬性要求</Text>
        <List
          size="small"
          dataSource={structured.hard_requirements}
          renderItem={(item) => <List.Item>{item}</List.Item>}
        />
        <Text strong>加分项</Text>
        <List
          size="small"
          dataSource={structured.nice_to_have_requirements}
          renderItem={(item) => <List.Item>{item}</List.Item>}
        />
      </Card>

      <Card type="inner" title="风险点" size="small">
        <List
          size="small"
          dataSource={structured.risk_points}
          renderItem={(item) => (
            <List.Item>
              <Space direction="vertical" size={0}>
                <Space>
                  <Tag color={SEVERITY_COLOR[item.severity] ?? "default"}>
                    {item.severity}
                  </Tag>
                  <Text strong>{item.title}</Text>
                </Space>
                <Text type="secondary">{item.detail}</Text>
              </Space>
            </List.Item>
          )}
        />
      </Card>

      {structured.skill_gaps.length > 0 && (
        <Card type="inner" title="技能差距" size="small">
          <List
            size="small"
            dataSource={structured.skill_gaps}
            renderItem={(item) => <List.Item>{item}</List.Item>}
          />
        </Card>
      )}

      {structured.interview_preparation.length > 0 && (
        <Card type="inner" title="面试准备" size="small">
          <List
            size="small"
            dataSource={structured.interview_preparation}
            renderItem={(item) => <List.Item>{item}</List.Item>}
          />
        </Card>
      )}

      <Card type="inner" title="薪酬 / 成长 / 稳定性" size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="薪酬">{structured.salary_note}</Descriptions.Item>
          <Descriptions.Item label="成长">{structured.growth_note}</Descriptions.Item>
          <Descriptions.Item label="稳定性">
            {structured.stability_note}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card type="inner" title="来源元数据" size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="分析 ID">{analysis.id}</Descriptions.Item>
          <Descriptions.Item label="产物 ID">{artifact.id}</Descriptions.Item>
          <Descriptions.Item label="产物类型">
            {artifact.artifact_type}
          </Descriptions.Item>
          <Descriptions.Item label="Prompt 版本">
            {artifact.prompt_version ?? "-"}
          </Descriptions.Item>
          <Descriptions.Item label="模型">
            {artifact.model_name ?? "-"}
          </Descriptions.Item>
          {artifact.source_ids?.provider ? (
            <Descriptions.Item label="Provider">
              {String(artifact.source_ids.provider)}
            </Descriptions.Item>
          ) : null}
          {artifact.source_ids?.resume_version_id ? (
            <Descriptions.Item label="简历版本">
              {String(artifact.source_ids.resume_version_id)}
            </Descriptions.Item>
          ) : null}
        </Descriptions>
        <Text type="secondary">
          以上分析为模型生成的草稿，非已完成的定制简历。
        </Text>
      </Card>
    </Space>
  );
}
