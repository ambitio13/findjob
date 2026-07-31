import { Link, Outlet, useLocation } from "react-router-dom";
import { ProLayout } from "@ant-design/pro-components";
import { DesktopOutlined, FileTextOutlined, UserOutlined } from "@ant-design/icons";

const menuRoutes = {
  path: "/",
  routes: [
    { path: "/", name: "概览", icon: <DesktopOutlined /> },
    { path: "/jobs", name: "职位", icon: <FileTextOutlined /> },
    { path: "/profile", name: "我的画像", icon: <UserOutlined /> },
  ],
};

export function AppLayout() {
  const location = useLocation();
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
    >
      <Outlet />
    </ProLayout>
  );
}
