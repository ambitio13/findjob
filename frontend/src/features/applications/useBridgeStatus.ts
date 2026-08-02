import { useCallback, useEffect, useRef, useState } from "react";
import { getBridgeStatus } from "@/api/client";
import type { BridgeStatusResponse } from "@/types";

/** Poll interval for bridge status (5s matches the userscript heartbeat). */
const BRIDGE_POLL_INTERVAL_MS = 5000;

/**
 * Polls the userscript bridge status endpoint so the UI can show whether a
 * Tampermonkey userscript is connected before the user starts a guided submit.
 *
 * The bridge endpoints are unauthenticated — the userscript cannot send
 * ``X-User-Id``. This hook only reads connection state; it never sends
 * credentials or session data.
 *
 * @param enabled When false, polling is skipped (e.g. when the guided-submit
 *   panel is not relevant to the current application status).
 */
export function useBridgeStatus(enabled = true): {
  status: BridgeStatusResponse | null;
  loading: boolean;
  refresh: () => void;
} {
  const [status, setStatus] = useState<BridgeStatusResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const tokenRef = useRef(0);

  const refresh = useCallback(async () => {
    const token = ++tokenRef.current;
    setLoading(true);
    try {
      const data = await getBridgeStatus();
      if (token === tokenRef.current) {
        setStatus(data);
      }
    } catch {
      // Network error or backend down — leave the previous status. The next
      // tick will retry. We do not surface this as a user-facing error.
    } finally {
      if (token === tokenRef.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const timer = setInterval(() => void refresh(), BRIDGE_POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [enabled, refresh]);

  return { status, loading, refresh };
}
