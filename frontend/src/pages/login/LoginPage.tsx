import { useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Card, Form, Input, Button, Typography, App, Divider, Alert } from "antd";
import { UserOutlined, LockOutlined } from "@ant-design/icons";
import { useAuth } from "@/features/auth/useAuth";
import { apiErrorMessage } from "@/api/client";

const { Title, Text } = Typography;

const DEMO_USERNAME = "demo";
const DEMO_PASSWORD = import.meta.env.VITE_DEMO_PASSWORD ?? "";

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const { login } = useAuth();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm();

  const from = (location.state as { from?: string } | null)?.from ?? "/";

  async function handleSubmit(values: { username: string; password: string }) {
    setLoading(true);
    try {
      await login({ username: values.username, password: values.password });
      message.success("登录成功");
      navigate(from, { replace: true });
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function handleDemoLogin() {
    if (!DEMO_PASSWORD) {
      message.error("演示账号未配置（缺少 VITE_DEMO_PASSWORD）");
      return;
    }
    setLoading(true);
    try {
      await login({ username: DEMO_USERNAME, password: DEMO_PASSWORD });
      message.success("已以演示账号登录");
      navigate(from, { replace: true });
    } catch (err) {
      message.error(apiErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#f0f2f5",
      }}
    >
      <Card style={{ width: 400, boxShadow: "0 2px 8px rgba(0,0,0,0.08)" }}>
        <div style={{ textAlign: "center", marginBottom: 24 }}>
          <Title level={3} style={{ marginBottom: 4 }}>
            求职智能助手
          </Title>
          <Text type="secondary">登录以体验完整演示</Text>
        </div>

        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          autoComplete="off"
        >
          <Form.Item
            name="username"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input
              prefix={<UserOutlined />}
              placeholder="用户名"
              size="large"
            />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="密码"
              size="large"
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 12 }}>
            <Button
              type="primary"
              htmlType="submit"
              size="large"
              block
              loading={loading}
            >
              登录
            </Button>
          </Form.Item>
        </Form>

        {DEMO_PASSWORD && (
          <>
            <Divider plain style={{ margin: "12px 0" }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                或
              </Text>
            </Divider>
            <Button
              size="large"
              block
              onClick={handleDemoLogin}
              loading={loading}
            >
              一键使用演示账号
            </Button>
          </>
        )}

        <Alert
          type="info"
          showIcon
          style={{ marginTop: 16, fontSize: 12 }}
          message="多访客提示"
          description="演示账号数据互相可见，推荐使用邀请码注册独立账号体验。"
        />
      </Card>
    </div>
  );
}
