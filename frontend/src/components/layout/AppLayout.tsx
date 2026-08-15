import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { ProLayout } from "@ant-design/pro-components";
import { DesktopOutlined, FileTextOutlined, SolutionOutlined, UserOutlined, AppstoreOutlined, LogoutOutlined } from "@ant-design/icons";
import { Button, Space, Typography } from "antd";
import { useAuth } from "@/features/auth/useAuth";

const { Text } = Typography;

const menuRoutes = {
  path: "/",
  routes: [
    { path: "/", name: "概览", icon: <DesktopOutlined /> },
    { path: "/jobs", name: "职位", icon: <FileTextOutlined /> },
    { path: "/resumes", name: "简历", icon: <SolutionOutlined /> },
    { path: "/applications", name: "投递", icon: <AppstoreOutlined /> },
    { path: "/profile", name: "我的画像", icon: <UserOutlined /> },
  ],
};

export function AppLayout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { displayName, username, logout } = useAuth();

  const handleLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <ProLayout
      title="求职智能助手"
      logo={false}
      layout="mix"
      route={menuRoutes}
      location={{ pathname: location.pathname }}
      menuItemRender={(item, dom) =>
        item.path ? <Link to={item.path}>{dom}</Link> : dom
      }
      fixSiderbar
      actionsRender={() => [
        <Space key="user" align="center">
          <Text type="secondary">{displayName ?? username}</Text>
          <Button
            type="text"
            icon={<LogoutOutlined />}
            onClick={handleLogout}
          >
            退出
          </Button>
        </Space>,
      ]}
    >
      <Outlet />
    </ProLayout>
  );
}
