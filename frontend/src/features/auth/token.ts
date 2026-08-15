// localStorage token read/write — the single entry point for auth state.

const TOKEN_KEY = "auth_token";
const USERNAME_KEY = "auth_username";
const DISPLAY_NAME_KEY = "auth_display_name";

/** Persist the bearer token and user identity after a successful login. */
export function setAuth(token: string, username: string, displayName: string): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USERNAME_KEY, username);
  localStorage.setItem(DISPLAY_NAME_KEY, displayName);
}

/** Read the stored bearer token, or null when not logged in. */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

/** The username of the logged-in user, or null. */
export function getUsername(): string | null {
  return localStorage.getItem(USERNAME_KEY);
}

/** The display name of the logged-in user, or null. */
export function getDisplayName(): string | null {
  return localStorage.getItem(DISPLAY_NAME_KEY);
}

/** Clear all auth state (logout / 401 expiry). */
export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USERNAME_KEY);
  localStorage.removeItem(DISPLAY_NAME_KEY);
}

/** Whether a token is currently stored (does not validate it server-side). */
export function isAuthenticated(): boolean {
  return getToken() !== null;
}
