import React, { Suspense } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { AppLayout } from "@/components/layout/AppLayout";
import { RouteLoading } from "@/components/common/RouteLoading";
import { DashboardPage } from "@/pages/dashboard/DashboardPage";

// Route-level code splitting: the heavy detail/pilot pages are lazy-loaded so
// they don't bloat the initial entry chunk. The shell (AppLayout + antd
// ConfigProvider + Dashboard) stays eager for a stable first paint.
const JobsPage = React.lazy(() =>
  import("@/pages/jobs/JobsPage").then((m) => ({ default: m.JobsPage })),
);
const JobDetailPage = React.lazy(() =>
  import("@/pages/jobs/JobDetailPage").then((m) => ({ default: m.JobDetailPage })),
);
const ResumesPage = React.lazy(() =>
  import("@/pages/resumes/ResumesPage").then((m) => ({
    default: m.ResumesPage,
  })),
);
const ResumeDetailPage = React.lazy(() =>
  import("@/pages/resumes/ResumeDetailPage").then((m) => ({
    default: m.ResumeDetailPage,
  })),
);
const ApplicationsPage = React.lazy(() =>
  import("@/pages/applications/ApplicationsPage").then((m) => ({
    default: m.ApplicationsPage,
  })),
);
const ProfilePage = React.lazy(() =>
  import("@/pages/profile/ProfilePage").then((m) => ({
    default: m.ProfilePage,
  })),
);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<DashboardPage />} />
            <Route
              path="/jobs"
              element={
                <Suspense fallback={<RouteLoading />}>
                  <JobsPage />
                </Suspense>
              }
            />
            <Route
              path="/jobs/:id"
              element={
                <Suspense fallback={<RouteLoading />}>
                  <JobDetailPage />
                </Suspense>
              }
            />
            <Route
              path="/resumes"
              element={
                <Suspense fallback={<RouteLoading />}>
                  <ResumesPage />
                </Suspense>
              }
            />
            <Route
              path="/resumes/:id"
              element={
                <Suspense fallback={<RouteLoading />}>
                  <ResumeDetailPage />
                </Suspense>
              }
            />
            <Route
              path="/applications"
              element={
                <Suspense fallback={<RouteLoading />}>
                  <ApplicationsPage />
                </Suspense>
              }
            />
            <Route
              path="/profile"
              element={
                <Suspense fallback={<RouteLoading />}>
                  <ProfilePage />
                </Suspense>
              }
            />
          </Route>
        </Routes>
      </BrowserRouter>
    </ConfigProvider>
  </React.StrictMode>,
);
