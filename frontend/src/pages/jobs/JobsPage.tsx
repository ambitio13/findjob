import { JobsTable } from "@/features/jobs/JobsTable";
import { HealthPanel } from "@/components/common/HealthPanel";
import { Col, Row } from "antd";

export function JobsPage() {
  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} md={17}>
        <JobsTable />
      </Col>
      <Col xs={24} md={7}>
        <HealthPanel />
      </Col>
    </Row>
  );
}
