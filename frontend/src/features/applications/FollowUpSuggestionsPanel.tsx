import { useCallback, useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  List,
  message,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  actionFollowUpSuggestion,
  apiErrorMessage,
  dismissFollowUpSuggestion,
  getMatchThresholdCalibration,
  listFollowUpSuggestions,
  runFollowUpScan,
} from "@/api/client";
import type {
  FollowUpSuggestionOut,
  MatchThresholdOut,
  SuggestionType,
} from "@/types";

const { Text } = Typography;

const TYPE_LABEL: Record<SuggestionType, string> = {
  change_opening_message: "换开场白",
  skill_gap_plan: "技能补齐",
  low_reply_rate_direction: "方向策略",
};

const TYPE_COLOR: Record<SuggestionType, string> = {
  change_opening_message: "orange",
  skill_gap_plan: "green",
  low_reply_rate_direction: "red",
};

function formatTime(ts: string | null): string {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

/**
 * Follow-up suggestions panel (Phase 5). Shows advisory nudges produced by
 * the daily scheduler scan (stale submissions, replied → skill-gap plan,
 * low reply-rate directions) and lets the user dismiss or mark them actioned.
 *
 * Suggestions are advisory only: acting on one re-enters the existing
 * generation + approval flow. This panel never triggers external effects.
 */
export function FollowUpSuggestionsPanel() {
  const [items, setItems] = useState<FollowUpSuggestionOut[]>([]);
  const [threshold, setThreshold] = useState<MatchThresholdOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [list, thr] = await Promise.all([
        listFollowUpSuggestions("pending"),
        getMatchThresholdCalibration(),
      ]);
      setItems(list.items);
      setThreshold(thr);
    } catch (err) {
      messageApi.warning(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [messageApi]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleScan = async () => {
    setScanning(true);
    try {
      const result = await runFollowUpScan();
      messageApi.success(
        result.created > 0
          ? `扫描完成，新增 ${result.created} 条跟进建议`
          : "扫描完成，暂无新的跟进建议",
      );
      await load();
    } catch (err) {
      messageApi.error(apiErrorMessage(err));
    } finally {
      setScanning(false);
    }
  };

  const resolve = async (
    id: string,
    action: "action" | "dismiss",
  ) => {
    try {
      if (action === "action") {
        await actionFollowUpSuggestion(id);
        messageApi.success("已标记为已处理");
      } else {
        await dismissFollowUpSuggestion(id);
        messageApi.success("已忽略该建议");
      }
      await load();
    } catch (err) {
      messageApi.error(apiErrorMessage(err));
    }
  };

  return (
    <Card
      title="今日跟进建议"
      size="small"
      extra={
        <Space>
          {threshold ? (
            <Tag color={threshold.source === "calibrated" ? "green" : "default"}>
              沟通阈值 {threshold.threshold}
              {threshold.source === "calibrated" ? "（已校准）" : "（默认）"}
            </Tag>
          ) : null}
          <Button size="small" onClick={() => void handleScan()} loading={scanning}>
            立即扫描
          </Button>
        </Space>
      }
    >
      {contextHolder}
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message="系统每天自动扫描一次投递与结果数据，给出跟进建议。建议仅供参考，任何对外动作仍需你在审批流程中确认。"
        />
        {loading ? (
          <Spin />
        ) : items.length === 0 ? (
          <Empty description="暂无待处理的跟进建议" />
        ) : (
          <List
            itemLayout="vertical"
            dataSource={items}
            renderItem={(s) => (
              <List.Item
                key={s.id}
                actions={[
                  <Popconfirm
                    key="action"
                    title="确认你已开始按此建议行动？"
                    onConfirm={() => void resolve(s.id, "action")}
                  >
                    <Button size="small" type="primary" ghost>
                      已处理
                    </Button>
                  </Popconfirm>,
                  <Button
                    key="dismiss"
                    size="small"
                    onClick={() => void resolve(s.id, "dismiss")}
                  >
                    忽略
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space>
                      <Tag color={TYPE_COLOR[s.suggestion_type]}>
                        {TYPE_LABEL[s.suggestion_type]}
                      </Tag>
                      <Text strong>{s.title}</Text>
                    </Space>
                  }
                  description={
                    <Space direction="vertical" size={0}>
                      {s.detail ? <Text type="secondary">{s.detail}</Text> : null}
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        创建于 {formatTime(s.created_at)}
                      </Text>
                    </Space>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Space>
    </Card>
  );
}
