import { HealthPanel } from "@/components/common/HealthPanel";
import { Card, Col, Row, Typography } from "antd";

const { Title, Paragraph } = Typography;

export function DashboardPage() {
  return (
    <div>
      <Title level={3}>求职智能助手 · 概览</Title>
      <Paragraph type="secondary">
        先把职位、画像和分析记录稳定串起来，后续功能会沿着这个用户边界继续生长。
      </Paragraph>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={12}>
          <HealthPanel />
        </Col>
        <Col xs={24} md={12}>
          <Card title="功能入口">
            <Paragraph>
              · 职位：录入和查看岗位
              <br />
              · 我的画像：维护求职偏好
              <br />· 健康状态：服务连通性
            </Paragraph>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
