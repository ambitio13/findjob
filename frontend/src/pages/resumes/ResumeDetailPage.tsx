import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  List,
  message,
  Space,
  Spin,
  Tag,
  Timeline,
  Typography,
} from "antd";
import {
  apiErrorMessage,
  getResume,
  listResumeVersions,
  reextractResumeFacts,
} from "@/api/client";
import type {
  ResumeDetailOut,
  ResumeFacts,
  ResumeVersionListItem,
} from "@/types";

const { Paragraph, Text } = Typography;

/** Color mapping for extraction status tags. */
function extractionStatusTag(status: string | undefined) {
  switch (status) {
    case "succeeded":
      return <Tag color="green">抽取成功</Tag>;
    case "failed":
      return <Tag color="red">抽取失败</Tag>;
    case "needs_confirmation":
      return <Tag color="orange">需确认</Tag>;
    case "not_run":
      return <Tag color="default">未抽取</Tag>;
    default:
      return <Tag color="default">未抽取</Tag>;
  }
}

/** Human-readable label for parser status. */
function parserStatusTag(status: string | undefined) {
  if (status === "parsed") return <Tag color="green">已解析</Tag>;
  if (status === "unsupported")
    return <Tag color="orange">不支持该格式的文本提取</Tag>;
  return <Tag>未知</Tag>;
}

/** Render a value or a muted dash when null/empty. */
function valueOrDash(v: unknown): React.ReactNode {
  if (v === null || v === undefined || v === "")
    return <Text type="secondary">-</Text>;
  return <>{String(v)}</>;
}

/** Render an array of strings as inline tags, or a muted dash. */
function tagsOrDash(items: string[] | null | undefined): React.ReactNode {
  if (!items || items.length === 0)
    return <Text type="secondary">-</Text>;
  return (
    <Space size={[4, 4]} wrap>
      {items.map((it, i) => (
        <Tag key={i}>{it}</Tag>
      ))}
    </Space>
  );
}

/** Read-only structured facts card. */
function FactsCards({ facts }: { facts: ResumeFacts | null | undefined }) {
  if (!facts) {
    return (
      <Empty
        description={<Text type="secondary">暂无结构化简历事实</Text>}
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size={16}>
      {/* 联系方式 */}
      <Card type="inner" title="联系方式" size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="姓名">
            {valueOrDash(facts.contact?.name)}
          </Descriptions.Item>
          <Descriptions.Item label="邮箱">
            {valueOrDash(facts.contact?.email)}
          </Descriptions.Item>
          <Descriptions.Item label="电话">
            {valueOrDash(facts.contact?.phone)}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* 教育经历 */}
      <Card type="inner" title="教育经历" size="small">
        {facts.education.length > 0 ? (
          <List
            size="small"
            dataSource={facts.education}
            renderItem={(edu) => (
              <List.Item>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="学校">
                    {valueOrDash(edu.school)}
                  </Descriptions.Item>
                  <Descriptions.Item label="学位">
                    {valueOrDash(edu.degree)}
                  </Descriptions.Item>
                  <Descriptions.Item label="专业">
                    {valueOrDash(edu.major)}
                  </Descriptions.Item>
                  <Descriptions.Item label="时间段">
                    {valueOrDash(edu.period)}
                  </Descriptions.Item>
                </Descriptions>
              </List.Item>
            )}
          />
        ) : (
          <Text type="secondary">-</Text>
        )}
      </Card>

      {/* 工作经历 */}
      <Card type="inner" title="工作经历" size="small">
        {facts.work_experience.length > 0 ? (
          <List
            size="small"
            dataSource={facts.work_experience}
            renderItem={(work) => (
              <List.Item>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="公司">
                    {valueOrDash(work.company)}
                  </Descriptions.Item>
                  <Descriptions.Item label="职位">
                    {valueOrDash(work.title)}
                  </Descriptions.Item>
                  <Descriptions.Item label="时间段">
                    {valueOrDash(work.period)}
                  </Descriptions.Item>
                  <Descriptions.Item label="概述">
                    {valueOrDash(work.summary)}
                  </Descriptions.Item>
                </Descriptions>
              </List.Item>
            )}
          />
        ) : (
          <Text type="secondary">-</Text>
        )}
      </Card>

      {/* 项目经历 */}
      <Card type="inner" title="项目经历" size="small">
        {facts.projects.length > 0 ? (
          <List
            size="small"
            dataSource={facts.projects}
            renderItem={(proj) => (
              <List.Item>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="项目名">
                    {valueOrDash(proj.name)}
                  </Descriptions.Item>
                  <Descriptions.Item label="角色">
                    {valueOrDash(proj.role)}
                  </Descriptions.Item>
                  <Descriptions.Item label="概述">
                    {valueOrDash(proj.summary)}
                  </Descriptions.Item>
                </Descriptions>
              </List.Item>
            )}
          />
        ) : (
          <Text type="secondary">-</Text>
        )}
      </Card>

      {/* 技能与方向 */}
      <Card type="inner" title="技能与方向" size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="技能">
            {tagsOrDash(facts.skills)}
          </Descriptions.Item>
          <Descriptions.Item label="工作年限">
            {valueOrDash(facts.years_of_experience)}
          </Descriptions.Item>
          <Descriptions.Item label="目标方向">
            {valueOrDash(facts.target_direction)}
          </Descriptions.Item>
          <Descriptions.Item label="期望地点">
            {tagsOrDash(facts.locations)}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* 亮点与优势 */}
      <Card type="inner" title="亮点与优势" size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="优势">
            {tagsOrDash(facts.strengths)}
          </Descriptions.Item>
          <Descriptions.Item label="亮点">
            {tagsOrDash(facts.highlights)}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* 待确认字段 */}
      {facts.uncertain_fields.length > 0 ? (
        <Card type="inner" title="待确认字段" size="small">
          <Timeline
            items={facts.uncertain_fields.map((u, i) => ({
              key: i,
              children: (
                <Space direction="vertical" size={0}>
                  <Text strong>{u.field}</Text>
                  {u.reason ? <Text type="secondary">{u.reason}</Text> : null}
                </Space>
              ),
            }))}
          />
        </Card>
      ) : null}
    </Space>
  );
}

/** Read-only profile draft derived from structured facts. */
function ProfileDraftPreview({
  facts,
}: {
  facts: ResumeFacts | null | undefined;
}) {
  if (!facts) {
    return (
      <Empty
        description={<Text type="secondary">暂无 Profile 草稿</Text>}
        image={Empty.PRESENTED_IMAGE_SIMPLE}
      />
    );
  }

  const name = facts.contact?.name ?? "（未识别姓名）";
  const years = facts.years_of_experience;
  const direction = facts.target_direction;
  const headlineParts: string[] = [];
  if (name) headlineParts.push(name);
  if (years !== null && years !== undefined)
    headlineParts.push(`${years} 年经验`);
  if (direction) headlineParts.push(direction);

  return (
    <Card type="inner" title="Profile 草稿预览（只读）" size="small">
      <Space direction="vertical" style={{ width: "100%" }} size={12}>
        <Paragraph strong style={{ marginBottom: 0 }}>
          {headlineParts.join(" · ")}
        </Paragraph>
        {facts.contact?.email || facts.contact?.phone ? (
          <Space size={16}>
            {facts.contact?.email ? <Text>{facts.contact.email}</Text> : null}
            {facts.contact?.phone ? <Text>{facts.contact.phone}</Text> : null}
          </Space>
        ) : null}
        {facts.skills.length > 0 ? (
          <div>
            <Text type="secondary">技能：</Text>
            {tagsOrDash(facts.skills)}
          </div>
        ) : null}
        {facts.locations.length > 0 ? (
          <div>
            <Text type="secondary">期望地点：</Text>
            {tagsOrDash(facts.locations)}
          </div>
        ) : null}
        {facts.highlights.length > 0 ? (
          <div>
            <Text type="secondary">亮点：</Text>
            <List
              size="small"
              dataSource={facts.highlights}
              renderItem={(h) => <List.Item>{h}</List.Item>}
            />
          </div>
        ) : null}
      </Space>
    </Card>
  );
}

export function ResumeDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [resume, setResume] = useState<ResumeDetailOut | null>(null);
  const [versions, setVersions] = useState<ResumeVersionListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reextracting, setReextracting] = useState(false);
  const [messageApi, contextHolder] = message.useMessage();

  const loadDetail = async (resumeId: string) => {
    const [detail, vers] = await Promise.all([
      getResume(resumeId),
      listResumeVersions(resumeId),
    ]);
    setResume(detail);
    setVersions(vers);
  };

  useEffect(() => {
    if (!id) return;
    let active = true;
    (async () => {
      try {
        await loadDetail(id);
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

  const handleReextract = async () => {
    if (!id || !resume?.latest_version) return;
    const versionId = resume.latest_version.id;
    setReextracting(true);
    try {
      const updated = await reextractResumeFacts(id, versionId);
      setResume(updated);
      messageApi.success("已重新抽取结构化事实");
    } catch (err) {
      messageApi.error(apiErrorMessage(err) ?? "重新解析失败");
    } finally {
      setReextracting(false);
    }
  };

  if (loading) return <Spin />;
  if (error || !resume) {
    return <Alert type="error" message="加载失败" description={error ?? undefined} />;
  }

  const latest = resume.latest_version;
  const parsedFacts = latest?.parsed_facts ?? null;
  const extraction = parsedFacts?._extraction;
  const facts = parsedFacts?.facts ?? null;
  const parserStatus = parsedFacts?._parser_status;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {contextHolder}

      <Card
        title="简历详情"
        extra={
          <Button
            loading={reextracting}
            onClick={handleReextract}
            disabled={!latest}
          >
            重新解析
          </Button>
        }
      >
        <Descriptions column={2}>
          <Descriptions.Item label="文件名">{resume.filename}</Descriptions.Item>
          <Descriptions.Item label="类型">
            {resume.mime_type ?? "-"}
          </Descriptions.Item>
          <Descriptions.Item label="存储路径">
            {resume.storage_uri ?? "-"}
          </Descriptions.Item>
          <Descriptions.Item label="创建时间">
            {resume.created_at ?? "-"}
          </Descriptions.Item>
          <Descriptions.Item label="最新版本">
            {latest ? `v${latest.version_no}` : "-"}
          </Descriptions.Item>
          <Descriptions.Item label="解析状态">
            {parserStatusTag(parserStatus)}
          </Descriptions.Item>
          <Descriptions.Item label="事实抽取状态">
            {extractionStatusTag(extraction?.status)}
          </Descriptions.Item>
          {extraction?.extracted_at ? (
            <Descriptions.Item label="抽取时间">
              {extraction.extracted_at}
            </Descriptions.Item>
          ) : null}
        </Descriptions>
      </Card>

      {/* 结构化事实 + Profile 草稿 */}
      <Card title="结构化简历事实">
        <Space direction="vertical" style={{ width: "100%" }} size={16}>
          <FactsCards facts={facts} />
          <ProfileDraftPreview facts={facts} />
        </Space>
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
