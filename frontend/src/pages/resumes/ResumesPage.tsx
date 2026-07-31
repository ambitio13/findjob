import { ResumesTable } from "@/features/resumes/ResumesTable";
import { ResumeUploadDragger } from "@/features/resumes/ResumeUploadModal";
import { Card, Col, Row } from "antd";
import { useState } from "react";

export function ResumesPage() {
  const [reloadToken, setReloadToken] = useState(0);

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} md={17}>
        <ResumesTable reloadToken={reloadToken} />
      </Col>
      <Col xs={24} md={7}>
        <Card title="上传简历">
          <ResumeUploadDragger onUploaded={() => setReloadToken((v) => v + 1)} />
        </Card>
      </Col>
    </Row>
  );
}
