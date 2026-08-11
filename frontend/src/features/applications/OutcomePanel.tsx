import { useCallback, useEffect, useState } from "react";
import { Button, Card, Empty, Input, message, Popconfirm, Space, Spin, Tag } from "antd";
import { apiErrorMessage, listOutcomes, recordOutcome } from "@/api/client";
import type { OutcomeOut, OutcomeType } from "@/types";

const OUTCOME_LABEL: Record<OutcomeType, string> = {
  replied: "已回复",
  rejected: "被拒",
  interview: "约面",
  offer: "拿到 Offer",
};

const OUTCOME_COLOR: Record<OutcomeType, string> = {
  replied: "blue",
  rejected: "red",
  interview: "green",
  offer: "gold",
};

const SOURCE_LABEL: Record<string, string> = {
  manual: "手动",
  userscript_observed: "userscript 观察",
};

function formatTime(ts: string | null): string {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString();
}

/**
 * Outcome feedback entry (Phase 1): one-click marking of what actually
 * happened after a submission (HR replied / rejected / interview / offer).
 *
 * The backend drives the state machine when it allows the transition and
 * stores evidence otherwise, so these buttons are the semi-automatic
 * fallback for the result-feedback loop. Evidence is a short summary only —
 * never chat content (privacy contract, mirrors backend ``outcome.py``).
 */
export function OutcomePanel({
  applicationId,
  onAfterChange,
}: {
  applicationId: string;
  /** Called after a successful record so the parent reloads the application
   * (status may have transitioned via the state machine). */
  onAfterChange: () => void;
}) {
  const [outcomes, setOutcomes] = useState<OutcomeOut[]>([]);
  const [loading, setLoading] = useState(false);
  const [recording, setRecording] = useState<OutcomeType | null>(null);
  const [evidence, setEvidence] = useState("");
  const [messageApi, contextHolder] = message.useMessage();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listOutcomes(applicationId);
      setOutcomes(data.items);
    } catch (err) {
      // A failed list load should not block one-click marking.
      messageApi.warning(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [applicationId, messageApi]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRecord = async (outcomeType: OutcomeType) => {
    try {
      setRecording(outcomeType);
      await recordOutcome(applicationId, {
        outcome_type: outcomeType,
        evidence: evidence.trim() || null,
      });
      messageApi.success(`已标记「${OUTCOME_LABEL[outcomeType]}」`);
      setEvidence("");
      await load();
      onAfterChange();
    } catch (err) {
      messageApi.error(apiErrorMessage(err));
    } finally {
      setRecording(null);
    }
  };

  return (
    <Card type="inner" title="结果标记" size="small">
      {contextHolder}
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Space wrap>
          <Popconfirm
            title="标记为已回复？"
            description="记录 HR 已回复，作为结果回流证据。"
            onConfirm={() => handleRecord("replied")}
            disabled={recording !== null}
          >
            <Button loading={recording === "replied"} disabled={recording !== null}>
              已回复
            </Button>
          </Popconfirm>
          <Popconfirm
            title="标记为约面？"
            description="记录进入面试；状态机允许时会自动流转到「面试中」。"
            onConfirm={() => handleRecord("interview")}
            disabled={recording !== null}
          >
            <Button loading={recording === "interview"} disabled={recording !== null}>
              约面
            </Button>
          </Popconfirm>
          <Popconfirm
            title="标记为被拒？"
            description="记录未通过；状态机允许时会自动流转到「已拒绝」。"
            onConfirm={() => handleRecord("rejected")}
            disabled={recording !== null}
          >
            <Button danger loading={recording === "rejected"} disabled={recording !== null}>
              被拒
            </Button>
          </Popconfirm>
          <Popconfirm
            title="标记为拿到 Offer？"
            onConfirm={() => handleRecord("offer")}
            disabled={recording !== null}
          >
            <Button loading={recording === "offer"} disabled={recording !== null}>
              拿到 Offer
            </Button>
          </Popconfirm>
        </Space>
        <Input.TextArea
          rows={2}
          placeholder="备注（可选）：如「HR 约了周三一面」。仅记录简短摘要，请勿粘贴聊天原文。"
          value={evidence}
          onChange={(e) => setEvidence(e.target.value)}
          maxLength={500}
          showCount
        />
        {loading && outcomes.length === 0 ? (
          <Spin size="small" />
        ) : outcomes.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="暂无结果记录，投递后记得回来标记进展"
          />
        ) : (
          <Space direction="vertical" size="small" style={{ width: "100%" }}>
            {outcomes.map((o) => (
              <Space key={o.id} wrap>
                <Tag color={OUTCOME_COLOR[o.outcome_type]}>
                  {OUTCOME_LABEL[o.outcome_type]}
                </Tag>
                <span style={{ color: "rgba(0,0,0,0.45)" }}>
                  {formatTime(o.occurred_at)}
                </span>
                <span style={{ color: "rgba(0,0,0,0.45)" }}>
                  {SOURCE_LABEL[o.source] ?? o.source}
                </span>
                {o.evidence ? <span>{o.evidence}</span> : null}
              </Space>
            ))}
          </Space>
        )}
      </Space>
    </Card>
  );
}
