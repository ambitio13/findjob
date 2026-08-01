import { useCallback, useEffect, useRef, useState } from "react";
import { getAgentRunDetail } from "@/api/client";
import type { AgentRunDetailOut } from "@/types";
import {
  AGENT_RUN_POLL_INTERVAL_MS,
  TERMINAL_AGENT_RUN_STATUSES,
  type AgentRunStatus,
} from "./status";

/**
 * Shared polling hook for agent-run-backed workflows.
 *
 * Polls ``GET /agent-runs/{runId}/detail`` until the run reaches a terminal
 * status (``succeeded`` / ``failed`` / ``not_run``). Handles cleanup on
 * unmount, token-based cancellation for re-submission, and non-fatal retry on
 * network blips — the three concerns that were previously duplicated in
 * ``JobCreateModal``, ``JobDetailPage``, and ``ResumeDetailPage``.
 *
 * Pass a ``runId`` to start polling; pass ``null`` to stop. The hook returns
 * the latest detail, whether polling is active, and a ``stop`` callback for
 * explicit cancellation (e.g. when the user navigates away or starts a new
 * run).
 */
export function useAgentRunPolling(
  runId: string | null,
  options?: {
    /** Polling interval in ms (default {@link AGENT_RUN_POLL_INTERVAL_MS}). */
    intervalMs?: number;
    /**
     * Called on each successful fetch with the latest detail. Useful for
     * side-effects like refreshing a sibling data list or hydrating form
     * fields from ``detail.result``.
     */
    onUpdate?: (detail: AgentRunDetailOut) => void;
    /** Called once when the run reaches a terminal status. */
    onTerminal?: (detail: AgentRunDetailOut) => void;
    /**
     * Called on each successful tick before checking terminal status. If it
     * returns ``true``, the run is treated as terminal and polling stops
     * (used by callers that need a custom terminal set, e.g. resume
     * extraction which polls a different endpoint).
     */
    isTerminal?: (detail: AgentRunDetailOut) => boolean;
  },
): {
  detail: AgentRunDetailOut | null;
  polling: boolean;
  stop: () => void;
} {
  const intervalMs = options?.intervalMs ?? AGENT_RUN_POLL_INTERVAL_MS;
  const [detail, setDetail] = useState<AgentRunDetailOut | null>(null);
  const [polling, setPolling] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tokenRef = useRef(0);

  // Keep latest callbacks in refs so the poll loop always sees fresh values
  // without needing to re-create the timer on every render.
  const onUpdateRef = useRef(options?.onUpdate);
  const onTerminalRef = useRef(options?.onTerminal);
  const isTerminalRef = useRef(options?.isTerminal);
  onUpdateRef.current = options?.onUpdate;
  onTerminalRef.current = options?.onTerminal;
  isTerminalRef.current = options?.isTerminal;

  const stop = useCallback(() => {
    tokenRef.current += 1;
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setPolling(false);
  }, []);

  // Start / restart polling whenever runId changes.
  useEffect(() => {
    if (!runId) {
      stop();
      setDetail(null);
      return;
    }

    const token = ++tokenRef.current;
    setPolling(true);

    const poll = async () => {
      try {
        const data = await getAgentRunDetail(runId);
        if (token !== tokenRef.current) return;
        setDetail(data);
        onUpdateRef.current?.(data);
        const terminal = isTerminalRef.current
          ? isTerminalRef.current(data)
          : TERMINAL_AGENT_RUN_STATUSES.has(data.status as AgentRunStatus);
        if (terminal) {
          if (token === tokenRef.current) {
            setPolling(false);
            onTerminalRef.current?.(data);
          }
          return;
        }
        if (token === tokenRef.current) {
          timerRef.current = setTimeout(poll, intervalMs);
        }
      } catch {
        // Network blips during polling are non-fatal; retry on next tick.
        if (token === tokenRef.current) {
          timerRef.current = setTimeout(poll, intervalMs);
        }
      }
    };

    timerRef.current = setTimeout(poll, intervalMs);

    return () => {
      tokenRef.current += 1;
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [runId, intervalMs, stop]);

  return { detail, polling, stop };
}
