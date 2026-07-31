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
import { message, Spin } from "antd";
import {
  apiErrorMessage,
  getCurrentUser,
  updateCurrentUser,
} from "@/api/client";
import type { UserProfile, UserProfileUpdate } from "@/types";

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

// Transform an inbound UserProfile into initial ProForm values. JSON list
// fields become arrays; the free-form constraints dict becomes a JSON string
// for editing in a textarea (MVP keeps constraints simple).
function toInitialValues(p: UserProfile): Record<string, unknown> {
  return {
    display_name: p.display_name,
    email: p.email ?? undefined,
    career_direction: p.career_direction ?? undefined,
    base_location: p.base_location ?? undefined,
    preferred_locations: p.preferred_locations ?? [],
    salary_min: p.salary_min ?? undefined,
    salary_max: p.salary_max ?? undefined,
    strengths: p.strengths ?? [],
    constraints:
      p.constraints && Object.keys(p.constraints).length > 0
        ? JSON.stringify(p.constraints, null, 2)
        : undefined,
  };
}

// Build the PATCH payload from submitted form values. Only fields the user
// actually edited are sent (undefined values are dropped), matching backend
// PATCH semantics where omitted fields are left untouched.
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
  if (values.constraints !== undefined) {
    const raw = values.constraints ? String(values.constraints).trim() : "";
    if (raw === "") {
      payload.constraints = null;
    } else {
      try {
        payload.constraints = JSON.parse(raw) as Record<string, unknown>;
      } catch {
        // Let the backend reject malformed JSON via 422 rather than crashing
        // the form; surface a clear message below.
        throw new Error("constraints 不是合法的 JSON");
      }
    }
  }
  return payload;
}

export function ProfileForm() {
  const [loading, setLoading] = useState(true);
  const [initialValues, setInitialValues] = useState<Record<string, unknown>>(
    {},
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const profile = await getCurrentUser();
        if (!cancelled) setInitialValues(toInitialValues(profile));
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
          await updateCurrentUser(payload);
          message.success("画像已更新");
          return true;
        } catch (err) {
          message.error(
            err instanceof Error ? err.message : apiErrorMessage(err),
          );
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
        <ProFormTextArea
          name="constraints"
          label="其他约束"
          width="xl"
          placeholder='例如：{"remote": true, "max_commute_min": 45}'
          fieldProps={{ autoSize: { minRows: 3, maxRows: 8 } }}
          tooltip="JSON 格式；留空则清除。可描述远程、通勤时长等偏好。"
        />
      </ProCard>
    </ProForm>
  );
}
