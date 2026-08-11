import { useMemo, useState } from "react";
import { Alert, Button, Card, Empty, Space, Typography, message } from "antd";
import type { ReadinessArtifactOut } from "@/types";
import { apiErrorMessage } from "@/api/client";

const { Text } = Typography;

/**
 * Phase 4 generate-and-copy (生成-复制) panel: the platform-agnostic delivery
 * path. Any application — regardless of platform or bridge availability — can
 * complete `generate opening message + targeted resume → one-click copy →
 * paste on the platform by hand`. Nothing here talks to the userscript bridge
 * or any browser adapter; the product stays fully useful when automation is
 * unavailable.
 *
 * Inputs are the artifacts already lifted by the parent (newest-first), so no
 * extra fetch is needed and the panel updates the moment a generation lands.
 */

interface OpeningMessageView {
  hook?: string;
  message: string;
  evidence?: string[];
  risk_note?: string | null;
}

interface TargetedResumeView {
  headline?: string;
  one_page_markdown: string;
}

function parseJson<T>(content: string): T | null {
  try {
    return JSON.parse(content) as T;
  } catch {
    return null;
  }
}

export function ManualSubmitPanel({
  artifacts,
}: {
  artifacts: ReadinessArtifactOut[];
}) {
  const [messageApi, contextHolder] = message.useMessage();
  const [copying, setCopying] = useState<string | null>(null);

  // Artifacts arrive newest-first; the first match per type is the latest.
  const opening = useMemo(() => {
    const artifact = artifacts.find(
      (a) => a.artifact_type === "hr_opening_message",
    );
    if (!artifact) return null;
    const parsed = parseJson<OpeningMessageView>(artifact.content);
    return parsed && typeof parsed.message === "string"
      ? { artifact, parsed }
      : null;
  }, [artifacts]);

  const resume = useMemo(() => {
    const artifact = artifacts.find((a) => a.artifact_type === "targeted_resume");
    if (!artifact) return null;
    const parsed = parseJson<TargetedResumeView>(artifact.content);
    return parsed && typeof parsed.one_page_markdown === "string"
      ? { artifact, parsed }
      : null;
  }, [artifacts]);

  const copyText = async (key: string, text: string, label: string) => {
    setCopying(key);
    try {
      await navigator.clipboard.writeText(text);
      messageApi.success(`${label}已复制，去平台粘贴即可`);
    } catch (err) {
      messageApi.error(apiErrorMessage(err));
    } finally {
      setCopying(null);
    }
  };

  // Combined copy bundle: opening message first (what the user sends in the
  // chat), then the one-page resume (what the user attaches/pastes).
  const copyAll = () => {
    const parts: string[] = [];
    if (opening) parts.push(`【开场白】\n${opening.parsed.message}`);
    if (resume) parts.push(`【一页简历】\n${resume.parsed.one_page_markdown}`);
    if (parts.length === 0) return;
    void copyText("all", parts.join("\n\n"), "全部材料");
  };

  return (
    <Card type="inner" title="生成-复制（手动投递）" size="small">
      {contextHolder}
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message="不依赖任何浏览器插件"
          description="复制下方生成物，自己去目标平台粘贴发送即可。任何平台的岗位都可以走这条路径。"
        />

        {opening ? (
          <Space direction="vertical" size={4} style={{ width: "100%" }}>
            <Text strong>开场白</Text>
            <Text type="secondary" style={{ whiteSpace: "pre-wrap" }}>
              {opening.parsed.message}
            </Text>
            <Button
              size="small"
              loading={copying === "opening"}
              onClick={() =>
                void copyText("opening", opening.parsed.message, "开场白")
              }
            >
              复制开场白
            </Button>
          </Space>
        ) : (
          <Text type="secondary">
            开场白尚未生成 —— 请在上方「就绪材料清单」生成 HR 开场白。
          </Text>
        )}

        {resume ? (
          <Space direction="vertical" size={4} style={{ width: "100%" }}>
            <Text strong>针对性一页简历</Text>
            <Text
              type="secondary"
              style={{ whiteSpace: "pre-wrap", maxHeight: 180, overflow: "auto", display: "block" }}
            >
              {resume.parsed.one_page_markdown}
            </Text>
            <Button
              size="small"
              loading={copying === "resume"}
              onClick={() =>
                void copyText(
                  "resume",
                  resume.parsed.one_page_markdown,
                  "一页简历",
                )
              }
            >
              复制一页简历
            </Button>
          </Space>
        ) : (
          <Text type="secondary">
            针对性简历尚未生成 —— 请在上方「就绪材料清单」生成针对性简历。
          </Text>
        )}

        {opening || resume ? (
          <Space>
            <Button
              type="primary"
              loading={copying === "all"}
              onClick={copyAll}
            >
              一键复制全部
            </Button>
            <Text type="secondary">
              粘贴完成后，可在下方状态操作中「标记已投递」。
            </Text>
          </Space>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="先生成开场白或针对性简历，即可一键复制"
          />
        )}
      </Space>
    </Card>
  );
}
