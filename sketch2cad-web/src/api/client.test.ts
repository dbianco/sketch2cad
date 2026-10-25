import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, CSRF_HEADER_NAME, createApiClient, get, post, readCookie } from "./client";

const CSRF_TOKEN = "csrf-token-value";

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, status = 200, contentType = "application/json"): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": contentType },
  });
}

function setCookie(name: string, value: string): void {
  document.cookie = `${name}=${value}; path=/`;
}

function clearCookie(name: string): void {
  document.cookie = `${name}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT`;
}

function lastCall(): { url: string; init: RequestInit; headers: Headers } {
  const call = fetchMock.mock.calls.at(-1);
  if (call === undefined) {
    throw new Error("fetch was not called");
  }
  const [url, init = {}] = call;
  return { url: String(url), init, headers: new Headers(init.headers) };
}

const caseFile = {
  code: "EXP-2026-0001",
  created_at: "2026-10-25T10:00:00Z",
  created_by: "8a7c0d4e-0000-4000-8000-000000000001",
  declared_location: "Calle Mayor 1",
  description: "Colisión en cruce",
  id: "8a7c0d4e-0000-4000-8000-000000000002",
  incident_date: "2026-10-24",
  incident_type: "traffic_collision",
  organization: null,
  status: "open",
} as const;

describe("api client", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    clearCookie("s2c_csrf");
    clearCookie("other");
    vi.unstubAllGlobals();
  });

  it("sends the CSRF cookie value as X-CSRF-Token on a mutation", async () => {
    setCookie("s2c_csrf", CSRF_TOKEN);
    fetchMock.mockResolvedValueOnce(jsonResponse(caseFile, 201));

    const created = await post("/api/v1/case-files", {
      body: {
        declared_location: "Calle Mayor 1",
        description: "Colisión en cruce",
        incident_date: "2026-10-24",
        incident_type: "traffic_collision",
      },
    });

    const { url, init, headers } = lastCall();
    expect(url).toBe("/api/v1/case-files");
    expect(init.method).toBe("POST");
    expect(headers.get(CSRF_HEADER_NAME)).toBe(CSRF_TOKEN);
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init.body))).toMatchObject({ incident_type: "traffic_collision" });
    expect(created.code).toBe("EXP-2026-0001");
  });

  it("sends a FormData body as is, without a JSON Content-Type", async () => {
    setCookie("s2c_csrf", CSRF_TOKEN);
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: "source-1" }, 201));
    const form = new FormData();
    form.append("client_sha256", "0".repeat(64));

    await post("/api/v1/case-files/{case_id}/sources", {
      path: { case_id: "case-1" },
      body: form,
    });

    const { url, init, headers } = lastCall();
    expect(url).toBe("/api/v1/case-files/case-1/sources");
    expect(init.body).toBe(form);
    expect(headers.get("Content-Type")).toBeNull();
    expect(headers.get(CSRF_HEADER_NAME)).toBe(CSRF_TOKEN);
  });

  it.each(["put", "patch", "delete"] as const)(
    "sends X-CSRF-Token on %s requests",
    async (method) => {
      setCookie("s2c_csrf", CSRF_TOKEN);
      fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
      const client = createApiClient();

      // No path of the current schema declares these methods, so the untyped request entry
      // point is exercised directly.
      const request = client.request as unknown as (m: string, p: string) => Promise<unknown>;
      await request(method, "/api/v1/case-files");

      const { init, headers } = lastCall();
      expect(init.method).toBe(method.toUpperCase());
      expect(headers.get(CSRF_HEADER_NAME)).toBe(CSRF_TOKEN);
    },
  );

  it("does not send X-CSRF-Token on a GET even when the cookie is set", async () => {
    setCookie("s2c_csrf", CSRF_TOKEN);
    fetchMock.mockResolvedValueOnce(jsonResponse({ items: [caseFile] }));

    const list = await get("/api/v1/case-files", { query: { q: "Mayor", limit: 10 } });

    const { url, init, headers } = lastCall();
    expect(url).toBe("/api/v1/case-files?q=Mayor&limit=10");
    expect(init.method).toBe("GET");
    expect(headers.has(CSRF_HEADER_NAME)).toBe(false);
    expect(init.body).toBeUndefined();
    expect(list.items).toHaveLength(1);
  });

  it("omits X-CSRF-Token on a mutation when the cookie is missing", async () => {
    setCookie("other", "value");
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    const result = await post("/api/v1/auth/logout");

    const { headers } = lastCall();
    expect(headers.has(CSRF_HEADER_NAME)).toBe(false);
    expect(result).toBeUndefined();
  });

  it("includes credentials on every request", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ user: { id: "u", username: "ana" } }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    await get("/api/v1/auth/me");
    expect(lastCall().init.credentials).toBe("include");
    await post("/api/v1/auth/logout");
    expect(lastCall().init.credentials).toBe("include");
  });

  it("interpolates and encodes path parameters", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(caseFile));

    await get("/api/v1/case-files/{case_id}", { path: { case_id: "a b/c" } });

    expect(lastCall().url).toBe("/api/v1/case-files/a%20b%2Fc");
  });

  it("prefixes paths with a configured base URL", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: "ok" }));
    const client = createApiClient({ baseUrl: "https://api.example.test/" });

    await client.get("/healthz");

    expect(lastCall().url).toBe("https://api.example.test/healthz");
  });

  it("parses a 401 problem+json response into an ApiError", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          type: "about:blank",
          title: "Unauthorized",
          status: 401,
          detail: "Invalid username or password.",
        },
        401,
        "application/problem+json",
      ),
    );

    const error = await post("/api/v1/auth/login", {
      body: { username: "ana", password: "wrong" },
    }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(401);
    expect(apiError.title).toBe("Unauthorized");
    expect(apiError.detail).toBe("Invalid username or password.");
    expect(apiError.message).toBe("Invalid username or password.");
    expect(apiError.lockedUntil).toBeUndefined();
  });

  it("exposes locked_until from a 423 problem+json response", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          type: "about:blank",
          title: "Locked",
          status: 423,
          detail: "The account is temporarily locked.",
          locked_until: "2026-10-25T10:15:00+00:00",
        },
        423,
        "application/problem+json",
      ),
    );

    const error = await post("/api/v1/auth/login", {
      body: { username: "ana", password: "secret" },
    }).catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(423);
    expect(apiError.title).toBe("Locked");
    expect(apiError.lockedUntil).toBe("2026-10-25T10:15:00+00:00");
    expect(apiError.problem.locked_until).toBe("2026-10-25T10:15:00+00:00");
  });

  it("exposes the errors extension of a 422 problem+json response", async () => {
    const errors = [{ loc: ["body", "username"], msg: "Field required", type: "missing" }];
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        {
          type: "about:blank",
          title: "Unprocessable Content",
          status: 422,
          detail: "The request failed validation.",
          errors,
        },
        422,
        "application/problem+json",
      ),
    );

    const error = await post("/api/v1/auth/login", {
      body: { username: "", password: "" },
    }).catch((caught: unknown) => caught);

    expect((error as ApiError).errors).toEqual(errors);
  });

  it("builds an ApiError from a non-JSON error response", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("Bad gateway", { status: 502, statusText: "Bad Gateway" }),
    );

    const error = await get("/healthz").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(502);
    expect(apiError.title).toBe("Bad Gateway");
    expect(apiError.detail).toBe("Bad gateway");
  });
});

describe("readCookie", () => {
  afterEach(() => {
    clearCookie("s2c_csrf");
  });

  it("returns the decoded cookie value", () => {
    setCookie("s2c_csrf", encodeURIComponent("a=b c"));

    expect(readCookie("s2c_csrf")).toBe("a=b c");
  });

  it("returns undefined when the cookie is absent", () => {
    expect(readCookie("s2c_csrf")).toBeUndefined();
  });
});
