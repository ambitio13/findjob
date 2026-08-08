import type { ReactNode } from "react";
import { Spin } from "antd";

/**
 * Stable loading fallback for lazy-loaded routes.
 *
 * Rendered inside <Suspense> while a route-level chunk is being fetched, so
 * the layout never shows a blank panel during navigation.
 */
export function RouteLoading(): ReactNode {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "60vh",
      }}
    >
      <Spin size="large" tip="加载中…" />
    </div>
  );
}
