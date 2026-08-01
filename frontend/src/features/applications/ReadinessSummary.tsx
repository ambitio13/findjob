import { Card, Descriptions, Empty, Tag, Typography } from "antd";
import type { ApplicationOut } from "@/types";
import {
  APP_STATUS_COLOR,
  APP_STATUS_LABEL,
  normalizeApplicationStatus,
} from "./status";

const { Text } = Typography;

interface Props {
  application: ApplicationOut;
  /** Whether an active generation run is in flight. */
  generating: boolean;
  /** Count of readiness artifacts currently persisted for the application. */
  artifactCount: number;
  /** Whether persisted artifacts are stale (source hash mismatch). */
  stale: boolean;
}

/**
 * One-line readiness summary at the top of the detail view.
 *
 * Surfaces the current status, whether materials exist, whether an active
 * generation is running, and a stale warning — so the user can tell at a
 * glance whether the application is ready, blocked, failed, or stale.
 */
export function ReadinessSummary({
  application,
  generating,
  artifactCount,
  stale,
}: Props) {
  const status = normalizeApplicationStatus(application.status);

  return (
    <Card type="inner" size="small">
      <Descriptions column={3} size="small">
        <Descriptions.Item label="状态">
          <Tag color={APP_STATUS_COLOR[status]}>{APP_STATUS_LABEL[status]}</Tag>
        </Descriptions.Item>
        <Descriptions.Item label="材料">
          {artifactCount > 0 ? (
            <Tag color={stale ? "orange" : "green"}>
              {artifactCount} 份{stale ? "（已过期）" : ""}
            </Tag>
          ) : (
            <Tag>无</Tag>
          )}
        </Descriptions.Item>
        <Descriptions.Item label="生成中">
          {generating ? <Tag color="processing">进行中</Tag> : <Tag>空闲</Tag>}
        </Descriptions.Item>
      </Descriptions>
      {stale ? (
        <Text type="warning">
          来源已变更，已有材料可能过期。可重新生成以匹配最新来源。
        </Text>
      ) : null}
      {status === "planned" && !application.resume_version_id ? (
        <Text type="secondary">尚未绑定简历版本，需先选择简历才能生成材料。</Text>
      ) : null}
      {artifactCount === 0 && !generating ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="尚无就绪材料，选择类型后生成。"
          style={{ marginTop: 8 }}
        />
      ) : null}
    </Card>
  );
}
