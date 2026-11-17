import { useSyncExternalStore } from "react";
import { ApiError, api, type ApiClient } from "../../api/client";
import type { components } from "../../api/schema";

/**
 * Session state of the current user.
 *
 * A tiny external store consumed through `useSyncExternalStore`, so no state library is
 * needed yet. Actions never throw: failures come back as typed results the UI can render.
 */

export type AuthUser = components["schemas"]["UserOut"];

export type AuthStatus = "unknown" | "loading" | "authenticated" | "anonymous";

export interface AuthState {
  status: AuthStatus;
  user: AuthUser | null;
}

export type LoginError =
  | { kind: "invalid_credentials" }
  | { kind: "locked"; lockedUntil: string | undefined }
  | { kind: "unknown" };

export type LoginResult = { ok: true; user: AuthUser } | { ok: false; error: LoginError };

export type ActionResult = { ok: true } | { ok: false };

export interface AuthStore {
  getState(): AuthState;
  subscribe(listener: () => void): () => void;
  /** `GET /auth/me`; a 401 (or any failure) leaves the user anonymous without an error. */
  loadSession(): Promise<ActionResult>;
  login(username: string, password: string): Promise<LoginResult>;
  /** `POST /auth/logout`, then reset to anonymous even if the request failed. */
  logout(): Promise<ActionResult>;
  /** Return to the initial `unknown` state (used by tests). */
  reset(): void;
}

const INITIAL_STATE: AuthState = { status: "unknown", user: null };

function toLoginError(error: unknown): LoginError {
  if (error instanceof ApiError) {
    if (error.status === 401) {
      return { kind: "invalid_credentials" };
    }
    if (error.status === 423) {
      return { kind: "locked", lockedUntil: error.lockedUntil };
    }
  }
  return { kind: "unknown" };
}

export function createAuthStore(client: ApiClient): AuthStore {
  let state: AuthState = INITIAL_STATE;
  let pendingSession: Promise<ActionResult> | null = null;
  const listeners = new Set<() => void>();

  function setState(next: AuthState): void {
    state = next;
    for (const listener of listeners) {
      listener();
    }
  }

  async function fetchSession(): Promise<ActionResult> {
    setState({ status: "loading", user: null });
    try {
      const { user } = await client.get("/api/v1/auth/me");
      setState({ status: "authenticated", user });
      return { ok: true };
    } catch {
      setState({ status: "anonymous", user: null });
      return { ok: false };
    }
  }

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    loadSession() {
      // Concurrent callers (for example StrictMode double effects) share one request.
      pendingSession ??= fetchSession().finally(() => {
        pendingSession = null;
      });
      return pendingSession;
    },
    async login(username, password) {
      try {
        const { user } = await client.post("/api/v1/auth/login", {
          body: { username, password },
        });
        setState({ status: "authenticated", user });
        return { ok: true, user };
      } catch (error) {
        return { ok: false, error: toLoginError(error) };
      }
    },
    async logout() {
      try {
        await client.post("/api/v1/auth/logout");
        return { ok: true };
      } catch {
        return { ok: false };
      } finally {
        setState({ status: "anonymous", user: null });
      }
    },
    reset() {
      pendingSession = null;
      setState(INITIAL_STATE);
    },
  };
}

/** Application-wide store backed by the default same-origin client. */
export const authStore: AuthStore = createAuthStore(api);

/** Subscribe a component to the application-wide auth state. */
export function useAuth(): AuthState {
  return useSyncExternalStore(authStore.subscribe, authStore.getState);
}
