import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Card, Descriptions, Spin, Alert, Typography, Button, message } from "antd";
import { apiErrorMessage, getJob, runManualJdAnalysisDemo } from "@/api/client";
import type { JobOut, ManualJdAnalysisDemoResponse } from "@/types";

const { Paragraph } = Typography;

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [demoResult, setDemoResult] = useState<ManualJdAnalysisDemoResponse | null>(null);
  const [running, setRunning] = useState(false);

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

  if (loading) return <Spin />;
  if (error || !job) {
    return <Alert type="error" message="加载失败" description={error ?? undefined} />;
  }

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

      <Card
        title="智能体运行 / 生成产物（占位）"
        extra={
          <Button
            type="primary"
            loading={running}
            onClick={async () => {
              setRunning(true);
              try {
                const result = await runManualJdAnalysisDemo(job.jd_raw);
                setDemoResult(result);
                message.success("分析运行完成");
              } catch (err) {
                message.error(apiErrorMessage(err));
              } finally {
                setRunning(false);
              }
            }}
          >
            运行 JD 分析（Demo）
          </Button>
        }
      >
        {demoResult ? (
          <Descriptions column={1}>
            <Descriptions.Item label="Run ID">{demoResult.agent_run.id}</Descriptions.Item>
            <Descriptions.Item label="状态">{demoResult.agent_run.status}</Descriptions.Item>
            <Descriptions.Item label="产物类型">{demoResult.artifact_type}</Descriptions.Item>
            <Descriptions.Item label="产物内容">{demoResult.content}</Descriptions.Item>
          </Descriptions>
        ) : (
          <Paragraph type="secondary">暂未运行。点击上方按钮运行 JD 分析 Demo 流程。</Paragraph>
        )}
      </Card>
    </div>
  );
}
