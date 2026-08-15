import { useCallback, useEffect, useState } from "react";
import {
  getToken,
  getUsername,
  getDisplayName,
  setAuth,
  clearAuth,
} from "./token";
import { login as apiLogin, register as apiRegister } from "@/api/client";
import type { AuthLoginRequest, AuthRegisterRequest } from "@/types";

export interface AuthState {
  isAuthenticated: boolean;
  username: string | null;
  displayName: string | null;
}

function readState(): AuthState {
  return {
    isAuthenticated: getToken() !== null,
    username: getUsername(),
    displayName: getDisplayName(),
  };
}

/**
 * Auth state hook. Provides login, register, and logout actions that update
 * both localStorage and the in-memory state so components re-render.
 */
export function useAuth() {
  const [state, setState] = useState<AuthState>(readState);

  const refresh = useCallback(() => setState(readState()), []);

  const login = useCallback(
    async (payload: AuthLoginRequest) => {
      const resp = await apiLogin(payload);
      // The token response doesn't include username/display_name; we store the
      // username from the login form and use it as the display name fallback.
      setAuth(resp.access_token, payload.username, payload.username);
      setState(readState());
      return resp;
    },
    [],
  );

  const register = useCallback(async (payload: AuthRegisterRequest) => {
    return apiRegister(payload);
  }, []);

  const logout = useCallback(() => {
    clearAuth();
    setState(readState());
  }, []);

  // Sync across tabs / windows.
  useEffect(() => {
    window.addEventListener("storage", refresh);
    return () => window.removeEventListener("storage", refresh);
  }, [refresh]);

  return { ...state, login, register, logout, refresh };
}
