import { useEffect, useState } from "react";
import {
  ProCard,
  ProForm,
  ProFormGroup,
  ProFormDigit,
  ProFormSelect,
  ProFormText,
  ProFormTextArea,
} from "@ant-design/pro-components";
import { Alert, message, Spin, Tag, Typography } from "antd";
import {
  apiErrorMessage,
  getCurrentUser,
  updateCurrentUser,
} from "@/api/client";
import type { ProfileConstraints, UserProfile, UserProfileUpdate } from "@/types";

const { Text } = Typography;

// Job-search direction options shared between the profile form and the jobs
// module. Kept inline for the MVP; move to a shared constants module if more
// screens need it.
const CAREER_DIRECTION_OPTIONS = [
  { label: "后端开发", value: "backend" },
  { label: "前端开发", value: "frontend" },
  { label: "全栈开发", value: "fullstack" },
  { label: "数据/算法", value: "data" },
  { label: "测试/质量", value: "qa" },
  { label: "运维/DevOps", value: "devops" },
  { label: "产品/项目管理", value: "pm" },
  { label: "其他", value: "other" },
];

// The 8 named constraint fields rendered as text inputs in the form. Each
// entry maps a storage key to its UI label, component type, and placeholder.
// The order here determines the on-screen layout.
const NAMED_CONSTRAINT_FIELDS: {
  key: keyof ProfileConstraints;
  label: string;
  placeholder: string;
  tooltip: string;
  textarea: boolean;
}[] = [
  {
    key: "deal_breakers",
    label: "一票否决项",
    placeholder: "例如：不接受 996、不接受频繁出差",
    tooltip: "哪些条件一旦不满足你就不会考虑这个机会？",
    textarea: true,
  },
  {
    key: "preferred_company_types",
    label: "偏好公司类型",
    placeholder: "例如：外企、上市公司、初创公司",
    tooltip: "你更倾向哪类公司？",
    textarea: false,
  },
  {
    key: "preferred_industries",
    label: "偏好行业",
    placeholder: "例如：互联网、金融科技、新能源",
    tooltip: "你感兴趣的行业方向。",
    textarea: false,
  },
  {
    key: "work_mode_preference",
    label: "工作模式偏好",
    placeholder: "例如：远程优先、混合办公、坐班",
    tooltip: "期望的工作模式。",
    textarea: false,
  },
  {
    key: "commute_preference",
    label: "通勤偏好",
    placeholder: "例如：单程不超过 45 分钟",
    tooltip: "对通勤时长或距离的要求。",
    textarea: false,
  },
  {
    key: "career_goals",
    label: "职业目标",
    placeholder: "例如：3 年内成为技术专家，带 5 人小团队",
    tooltip: "你希望未来 1-3 年达成的目标。",
    textarea: true,
  },
  {
    key: "resume_tailoring_notes",
    label: "简历投递备注",
    placeholder: "例如：投外企时突出英语能力",
    tooltip: "投递简历时需要特别调整或强调的内容。",
    textarea: true,
  },
  {
    key: "availability_notes",
    label: "到岗/签证备注",
    placeholder: "例如：可立即到岗、需 H1B 签证",
    tooltip: "到岗时间或工作签证相关的说明。",
    textarea: false,
  },
];

// Transform an inbound UserProfile into initial ProForm values. Named
// constraint fields are flattened to top-level form keys so each input can
// bind directly.
function toInitialValues(p: UserProfile): Record<string, unknown> {
  const nc = p.named_constraints ?? ({} as ProfileConstraints);
  const values: Record<string, unknown> = {
    display_name: p.display_name,
    email: p.email ?? undefined,
    career_direction: p.career_direction ?? undefined,
    base_location: p.base_location ?? undefined,
    preferred_locations: p.preferred_locations ?? [],
    salary_min: p.salary_min ?? undefined,
    salary_max: p.salary_max ?? undefined,
    strengths: p.strengths ?? [],
  };
  for (const f of NAMED_CONSTRAINT_FIELDS) {
    values[f.key] = nc[f.key] ?? undefined;
  }
  return values;
}

// Build the PATCH payload from submitted form values. Only fields the user
// actually edited are sent (undefined values are dropped), matching backend
// PATCH semantics where omitted fields are left untouched. For named
// constraint fields, an empty string clears the key (sends null) while a
// non-empty string sets it.
function toUpdatePayload(
  values: Record<string, unknown>,
): UserProfileUpdate {
  const payload: UserProfileUpdate = {};
  if (values.display_name !== undefined)
    payload.display_name = String(values.display_name);
  if (values.email !== undefined)
    payload.email = values.email ? String(values.email) : null;
  if (values.career_direction !== undefined)
    payload.career_direction = values.career_direction
      ? String(values.career_direction)
      : null;
  if (values.base_location !== undefined)
    payload.base_location = values.base_location
      ? String(values.base_location)
      : null;
  if (values.preferred_locations !== undefined)
    payload.preferred_locations = (values.preferred_locations as string[]) ?? null;
  if (values.salary_min !== undefined)
    payload.salary_min =
      values.salary_min !== undefined && values.salary_min !== null
        ? Number(values.salary_min)
        : null;
  if (values.salary_max !== undefined)
    payload.salary_max =
      values.salary_max !== undefined && values.salary_max !== null
        ? Number(values.salary_max)
        : null;
  if (values.strengths !== undefined)
    payload.strengths = (values.strengths as string[]) ?? null;

  // Collect named constraint fields that were present in the form. Only
  // include keys the user interacted with (value !== undefined).
  const nc: Partial<ProfileConstraints> = {};
  let hasNamed = false;
  for (const f of NAMED_CONSTRAINT_FIELDS) {
    if (values[f.key] === undefined) continue;
    hasNamed = true;
    const raw = String(values[f.key]).trim();
    nc[f.key] = raw === "" ? null : raw;
  }
  if (hasNamed) payload.named_constraints = nc;

  return payload;
}

export function ProfileForm() {
  const [loading, setLoading] = useState(true);
  const [initialValues, setInitialValues] = useState<Record<string, unknown>>(
    {},
  );
  const [legacyConstraints, setLegacyConstraints] = useState<
    Record<string, unknown> | null
  >(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const profile = await getCurrentUser();
        if (!cancelled) {
          setInitialValues(toInitialValues(profile));
          setLegacyConstraints(profile.legacy_constraints ?? null);
        }
      } catch (err) {
        message.error(apiErrorMessage(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (loading) {
    return (
      <div style={{ textAlign: "center", padding: 48 }}>
        <Spin />
      </div>
    );
  }

  const legacyKeys = legacyConstraints
    ? Object.keys(legacyConstraints)
    : [];

  return (
    <ProForm<UserProfileUpdate>
      layout="horizontal"
      initialValues={initialValues}
      submitter={{
        searchConfig: { submitText: "保存", resetText: "重置" },
        resetButtonProps: { style: { display: "none" } },
      }}
      onFinish={async (values) => {
        try {
          const payload = toUpdatePayload(
            values as unknown as Record<string, unknown>,
          );
          const updated = await updateCurrentUser(payload);
          setLegacyConstraints(updated.legacy_constraints ?? null);
          message.success("画像已更新");
          return true;
        } catch (err) {
          message.error(apiErrorMessage(err));
          return false;
        }
      }}
    >
      <ProCard title="基本信息" bordered headerBordered style={{ marginBottom: 16 }}>
        <ProFormGroup>
          <ProFormText
            name="display_name"
            label="昵称"
            width="md"
            rules={[{ required: true, message: "请输入昵称" }]}
          />
          <ProFormText
            name="email"
            label="邮箱"
            width="md"
            fieldProps={{ type: "email" }}
          />
          <ProFormSelect
            name="career_direction"
            label="求职方向"
            width="md"
            options={CAREER_DIRECTION_OPTIONS}
            allowClear
          />
          <ProFormText name="base_location" label="常驻地" width="md" />
        </ProFormGroup>
      </ProCard>

      <ProCard title="求职偏好" bordered headerBordered>
        <ProFormGroup>
          <ProFormSelect
            name="preferred_locations"
            label="期望城市"
            width="md"
            fieldProps={{ mode: "tags", tokenSeparators: [","] }}
            allowClear
          />
          <ProFormDigit
            name="salary_min"
            label="最低薪资"
            width="sm"
            fieldProps={{ min: 0, step: 1000, addonAfter: "元/月" }}
          />
          <ProFormDigit
            name="salary_max"
            label="最高薪资"
            width="sm"
            fieldProps={{ min: 0, step: 1000, addonAfter: "元/月" }}
          />
          <ProFormSelect
            name="strengths"
            label="核心优势"
            width="md"
            fieldProps={{ mode: "tags", tokenSeparators: [","] }}
            allowClear
          />
        </ProFormGroup>
      </ProCard>

      <ProCard
        title="求职约束与备注"
        bordered
        headerBordered
        style={{ marginTop: 16 }}
      >
        <ProFormGroup labelLayout="default" style={{ flexDirection: "column" }}>
          {NAMED_CONSTRAINT_FIELDS.map((f) =>
            f.textarea ? (
              <ProFormTextArea
                key={f.key}
                name={f.key}
                label={f.label}
                width="xl"
                placeholder={f.placeholder}
                tooltip={f.tooltip}
                fieldProps={{ autoSize: { minRows: 2, maxRows: 6 } }}
              />
            ) : (
              <ProFormText
                key={f.key}
                name={f.key}
                label={f.label}
                width="xl"
                placeholder={f.placeholder}
                tooltip={f.tooltip}
                allowClear
              />
            ),
          )}
        </ProFormGroup>

        {legacyKeys.length > 0 ? (
          <Alert
            type="info"
            showIcon
            style={{ marginTop: 16 }}
            message="历史遗留约束（只读）"
            description={
              <div>
                <Text type="secondary">
                  以下约束来自旧版数据，不支持在此表单编辑。如需修改请清除后使用上方字段重新填写。
                </Text>
                <div style={{ marginTop: 8 }}>
                  {legacyKeys.map((k) => (
                    <Tag key={k} style={{ marginBottom: 4 }}>
                      {k}: {JSON.stringify(legacyConstraints![k])}
                    </Tag>
                  ))}
                </div>
              </div>
            }
          />
        ) : null}
      </ProCard>
    </ProForm>
  );
}
