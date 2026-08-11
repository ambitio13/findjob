import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Card, Col, Empty, Row, Spin, Statistic, Table, Typography } from "antd";
import { HealthPanel } from "@/components/common/HealthPanel";
import { apiErrorMessage, getFunnelMetrics } from "@/api/client";
import type {
  FunnelMetricsOut,
  MatchScoreBucketOut,
  OpeningPromptBucketOut,
} from "@/types";

const { Title, Paragraph } = Typography;

function formatRate(rate: number | null): string {
  if (rate === null) return "-";
  return `${(rate * 100).toFixed(1)}%`;
}

/**
 * Submission funnel panel (Phase 1 feedback loop). Surfaces the outcome
 * calibration view: totals, reply/interview rates, and per-match-score
 * buckets — the evidence surface for tuning the communicate gate and the
 * opening-message prompts.
 */
function FunnelMetricsPanel() {
  const [metrics, setMetrics] = useState<FunnelMetricsOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setMetrics(await getFunnelMetrics());
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <Spin />;
  if (error) return <Empty description={error} />;
  if (!metrics) return null;

  const bucketColumns = [
    { title: "匹配分区间", dataIndex: "bucket", key: "bucket" },
    { title: "投递数", dataIndex: "applications", key: "applications" },
    { title: "收到回复", dataIndex: "replied", key: "replied" },
    { title: "进入面试", dataIndex: "interviews", key: "interviews" },
  ];

  const promptColumns = [
    { title: "开场白 Prompt 版本", dataIndex: "prompt_version", key: "prompt_version" },
    { title: "投递数", dataIndex: "applications", key: "applications" },
    { title: "收到回复", dataIndex: "replied", key: "replied" },
    { title: "进入面试", dataIndex: "interviews", key: "interviews" },
  ];

  return (
    <Card
      title="投递漏斗 · 结果回流"
      extra={
        <a onClick={() => void load()} style={{ cursor: "pointer" }}>
          刷新
        </a>
      }
    >
      <Row gutter={[16, 16]}>
        <Col span={6}>
          <Statistic title="投递记录总数" value={metrics.applications_total} />
        </Col>
        <Col span={6}>
          <Statistic title="已投递" value={metrics.submitted} />
        </Col>
        <Col span={6}>
          <Statistic
            title="回复率"
            value={formatRate(metrics.reply_rate)}
            suffix={
              metrics.submitted > 0
                ? `(${metrics.with_reply}/${metrics.submitted})`
                : undefined
            }
          />
        </Col>
        <Col span={6}>
          <Statistic
            title="约面率"
            value={formatRate(metrics.interview_rate)}
            suffix={
              metrics.submitted > 0
                ? `(${metrics.interviews}/${metrics.submitted})`
                : undefined
            }
          />
        </Col>
        <Col span={6}>
          <Statistic title="Offer" value={metrics.offers} />
        </Col>
        <Col span={6}>
          <Statistic title="被拒" value={metrics.rejected} />
        </Col>
      </Row>
      {metrics.submitted === 0 ? (
        <Paragraph type="secondary" style={{ marginTop: 16 }}>
          还没有已投递的记录。去 <Link to="/applications">投递记录</Link>{" "}
          完成一次投递后，这里的回复率才会开始累计。
        </Paragraph>
      ) : metrics.by_match_score.length > 0 ? (
        <>
          <Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 8 }}>
            按匹配分分桶：用于校准沟通门槛（COMMUNICATE_MIN_SCORE）与开场白效果。
          </Paragraph>
          <Table<MatchScoreBucketOut>
            size="small"
            rowKey="bucket"
            pagination={false}
            columns={bucketColumns}
            dataSource={metrics.by_match_score}
          />
        </>
      ) : null}
      {metrics.submitted > 0 && metrics.by_opening_prompt.length > 0 ? (
        <>
          <Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 8 }}>
            按开场白 prompt 版本分桶：回答“哪版开场白回复率最高”。
          </Paragraph>
          <Table<OpeningPromptBucketOut>
            size="small"
            rowKey="prompt_version"
            pagination={false}
            columns={promptColumns}
            dataSource={metrics.by_opening_prompt}
          />
        </>
      ) : null}
    </Card>
  );
}

export function DashboardPage() {
  return (
    <div>
      <Title level={3}>求职智能助手 · 概览</Title>
      <Paragraph type="secondary">
        先把职位、画像和分析记录稳定串起来，后续功能会沿着这个用户边界继续生长。
      </Paragraph>
      <Row gutter={[16, 16]}>
        <Col xs={24}>
          <FunnelMetricsPanel />
        </Col>
        <Col xs={24} md={12}>
          <HealthPanel />
        </Col>
        <Col xs={24} md={12}>
          <Card title="功能入口">
            <Paragraph>
              · 职位：录入和查看岗位
              <br />
              · 我的画像：维护求职偏好
              <br />
              · 投递记录：准备材料与标记结果
              <br />· 健康状态：服务连通性
            </Paragraph>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
