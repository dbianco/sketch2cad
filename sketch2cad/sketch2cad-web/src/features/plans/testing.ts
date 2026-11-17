// Test helpers of the plan pages: API fixtures and a fetch stub routed by method and path.
import { vi } from "vitest";
import type { PlanOut } from "../../editor/plan/planStore";
import { authStore } from "../auth/authStore";
import type { UserRole } from "./TransitionBar";

export const PLAN_ID = "00000000-0000-4000-8000-0000000000a1";
export const CASE_ID = "00000000-0000-4000-8000-0000000000c1";
export const REVISION_ID = "00000000-0000-4000-8000-0000000000b1";
export const SOURCE_ID = "00000000-0000-4000-8000-0000000000d1";
export const USER_ID = "00000000-0000-4000-8000-0000000000e1";

export function planOut(overrides: Partial<PlanOut> = {}): PlanOut {
  return {
    id: PLAN_ID,
    case_file_id: CASE_ID,
    created_at: "2026-10-25T00:00:00Z",
    current_revision: { id: REVISION_ID, number: 2, state: "draft" },
    current_revision_id: REVISION_ID,
    lease: null,
    metric_status: "approximate",
    primary_source_id: SOURCE_ID,
    source_mode: "schematic_sketch",
    status: "EDITING",
    title: "Plano 1",
    validated_revision_id: null,
    calibration: null,
    ...overrides,
  };
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

/** Responses keyed by `"METHOD /path"` (no query); a function gets the request init. */
export type Routes = Record<string, Response | ((init: RequestInit | undefined) => Response)>;

export const fetchMock = vi.fn<typeof fetch>();

/** Stub `fetch` with `routes`; unknown requests answer 404. */
export function stubFetch(routes: Routes): void {
  fetchMock.mockImplementation((input, init) => {
    const path = new URL(String(input), "http://localhost").pathname;
    const route = routes[`${init?.method ?? "GET"} ${path}`];
    const response = typeof route === "function" ? route(init) : route?.clone();
    return Promise.resolve(response ?? jsonResponse({ title: "Not Found" }, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
}

/** Requests sent with `method` to `path`, with their parsed JSON bodies. */
export function sent(method: string, path: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([input, init]) => (init?.method ?? "GET") === method && String(input).startsWith(path))
    .map(([, init]) => (typeof init?.body === "string" ? JSON.parse(init.body) : undefined));
}

/** Sign in as a user with `role` through a stubbed `GET /auth/me`. */
export async function signInAs(role: UserRole, routes: Routes = {}): Promise<void> {
  stubFetch({
    ...routes,
    "GET /api/v1/auth/me": jsonResponse({
      user: { id: USER_ID, username: role, display_name: role, role },
    }),
  });
  await authStore.loadSession();
}
