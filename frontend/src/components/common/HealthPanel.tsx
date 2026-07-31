import { Card, Descriptions, Tag, Alert, Spin } from "antd";
import { useHealth } from "@/features/agent-runs/useHealth";

const statusColor = (s: string | null | undefined) => {
  if (!s) return "default";
  if (s === "ok") return "green";
  if (s.startsWith("error")) return "red";
  return "blue";
};

export function HealthPanel() {
  const { health, error, loading } = useHealth();

  if (loading) {
    return (
      <Card title="后端状态">
        <Spin />
      </Card>
    );
  }

  if (error || !health) {
    return (
      <Card title="后端状态">
        <Alert type="error" message="无法连接后端" description={error ?? undefined} />
      </Card>
    );
  }

  return (
    <Card title="后端状态">
      <Descriptions column={1} size="small">
        <Descriptions.Item label="应用">
          {health.app} <Tag>{health.env}</Tag> <Tag>v{health.version}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="状态">
          <Tag color={statusColor(health.status)}>{health.status}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="数据库">
          <Tag color={statusColor(health.db)}>{health.db ?? "unknown"}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="Redis">
          <Tag color={statusColor(health.redis)}>{health.redis ?? "unknown"}</Tag>
        </Descriptions.Item>
      </Descriptions>
    </Card>
  );
}
