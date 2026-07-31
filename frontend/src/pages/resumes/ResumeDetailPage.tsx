import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Alert, Card, Descriptions, Spin, Tag, Typography } from "antd";
import { apiErrorMessage, getResume, listResumeVersions } from "@/api/client";
import type { ResumeDetailOut, ResumeVersionListItem } from "@/types";

const { Paragraph } = Typography;

export function ResumeDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [resume, setResume] = useState<ResumeDetailOut | null>(null);
  const [versions, setVersions] = useState<ResumeVersionListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let active = true;
    (async () => {
      try {
        const [detail, vers] = await Promise.all([
          getResume(id),
          listResumeVersions(id),
        ]);
        if (active) {
          setResume(detail);
          setVersions(vers);
        }
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
  if (error || !resume) {
    return <Alert type="error" message="加载失败" description={error ?? undefined} />;
  }

  const latest = resume.latest_version;
  const parserStatus = latest?.parsed_facts?.["_parser_status"];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card title="简历详情">
        <Descriptions column={2}>
          <Descriptions.Item label="文件名">{resume.filename}</Descriptions.Item>
          <Descriptions.Item label="类型">{resume.mime_type ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="存储路径">{resume.storage_uri ?? "-"}</Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {resume.created_at ?? "-"}
          </Descriptions.Item>
          <Descriptions.Item label="最新版本">
            {latest ? `v${latest.version_no}` : "-"}
          </Descriptions.Item>
          <Descriptions.Item label="解析状态">
            {parserStatus === "parsed" ? (
              <Tag color="green">已解析</Tag>
            ) : parserStatus === "unsupported" ? (
              <Tag color="orange">不支持该格式的文本提取</Tag>
            ) : (
              <Tag>未知</Tag>
            )}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="解析文本（raw_text）">
        {latest?.raw_text ? (
          <Paragraph
            style={{ whiteSpace: "pre-wrap", maxHeight: 480, overflow: "auto" }}
          >
            {latest.raw_text}
          </Paragraph>
        ) : (
          <Paragraph type="secondary">
            暂无解析文本。该格式暂不支持文本提取，原件已留存。
          </Paragraph>
        )}
      </Card>

      <Card title="版本历史">
        {versions.length > 0 ? (
          <Descriptions column={1}>
            {versions.map((v) => (
              <Descriptions.Item key={v.id} label={`v${v.version_no}`}>
                {v.created_at ?? "-"}
                {v.parser_name ? (
                  <Tag style={{ marginLeft: 8 }}>{v.parser_name}</Tag>
                ) : null}
                {v.parser_status === "parsed" ? (
                  <Tag color="green" style={{ marginLeft: 8 }}>
                    已解析
                  </Tag>
                ) : v.parser_status === "unsupported" ? (
                  <Tag color="orange" style={{ marginLeft: 8 }}>
                    未提取
                  </Tag>
                ) : null}
              </Descriptions.Item>
            ))}
          </Descriptions>
        ) : (
          <Paragraph type="secondary">暂无版本记录。</Paragraph>
        )}
      </Card>
    </div>
  );
}
