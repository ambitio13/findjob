import { Card, Descriptions, Tag, Typography } from "antd";
import type { ApplicationOut, ReadinessArtifactOut } from "@/types";
import { ARTIFACT_TYPE_LABEL } from "./status";

const { Text } = Typography;

interface Props {
  application: ApplicationOut;
  /** The latest artifact (any type), used to read its source hash. */
  latestArtifact: ReadinessArtifactOut | null;
  /** Whether the artifact's source hash differs from the application's. */
  stale: boolean;
}

/**
 * Renders the readiness source snapshot: the identifiers and version stamps
 * that were hashed when the latest artifact was generated. This is the
 * "provenance" panel — it explains *what* the AI saw, without showing raw
 * JD or resume text.
 *
 * The snapshot lives on ``ApplicationOut.readiness_snapshot`` (written by the
 * readiness worker) and on each artifact's ``source_ids``. We prefer the
 * application snapshot (the durable contract) and fall back to the artifact's
 * source_ids for display.
 */
export function SourceSnapshotPanel({ application, latestArtifact, stale }: Props) {
  const snapshot = application.readiness_snapshot;
  const sourceHash =
    typeof snapshot === "object" && snapshot !== null && typeof snapshot.source_hash === "string"
      ? snapshot.source_hash
      : latestArtifact?.source_ids && typeof latestArtifact.source_ids.source_hash === "string"
        ? latestArtifact.source_ids.source_hash
        : null;

  const artifactType =
    typeof snapshot === "object" && snapshot !== null && typeof snapshot.artifact_type === "string"
      ? snapshot.artifact_type
      : latestArtifact?.artifact_type ?? null;

  return (
    <Card type="inner" title="来源快照" size="small">
      {sourceHash ? (
        <Descriptions column={1} size="small">
          <Descriptions.Item label="来源哈希">
            <Tag color={stale ? "orange" : "default"}>
              <Text code style={{ fontSize: 12 }}>
                {sourceHash.slice(0, 16)}…
              </Text>
            </Tag>
            {stale ? (
              <Text type="warning" style={{ marginLeft: 8 }}>
                与当前来源不一致
              </Text>
            ) : null}
          </Descriptions.Item>
          {artifactType ? (
            <Descriptions.Item label="最近产物类型">
              {ARTIFACT_TYPE_LABEL[artifactType] ?? artifactType}
            </Descriptions.Item>
          ) : null}
          <Descriptions.Item label="职位 ID">
            <Text code>{application.job_id}</Text>
          </Descriptions.Item>
          <Descriptions.Item label="简历版本 ID">
            <Text code>{application.resume_version_id ?? "-"}</Text>
          </Descriptions.Item>
        </Descriptions>
      ) : (
        <Text type="secondary">尚无来源快照——生成首份材料后将记录来源哈希。</Text>
      )}
    </Card>
  );
}
