import { useEffect, useState } from "react";
import { getHealth } from "@/api/client";
import type { HealthResponse } from "@/types";
import { apiErrorMessage } from "@/api/client";

const POLL_MS = 15000;

export function useHealth() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    async function fetchHealth() {
      try {
        const data = await getHealth();
        if (active) {
          setHealth(data);
          setError(null);
        }
      } catch (err) {
        if (active) setError(apiErrorMessage(err));
      } finally {
        if (active) setLoading(false);
      }
    }
    fetchHealth();
    const timer = setInterval(fetchHealth, POLL_MS);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  return { health, error, loading };
}
