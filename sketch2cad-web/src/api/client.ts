import type { paths } from "./schema";

/**
 * Typed wrapper over `fetch` for the Sketch2CAD API (decision D-7).
 *
 * Request and response types come from the generated `schema.d.ts`. Every request sends the
 * session cookie (`credentials: "include"`), and mutations carry the `X-CSRF-Token` header
 * read from the `s2c_csrf` cookie (decision D-8). Errors are parsed from
 * `application/problem+json` into an `ApiError`.
 */

export const CSRF_COOKIE_NAME = "s2c_csrf";
export const CSRF_HEADER_NAME = "X-CSRF-Token";

export type HttpMethod = "get" | "put" | "post" | "delete" | "patch" | "head";

const CSRF_METHODS: ReadonlySet<HttpMethod> = new Set(["post", "put", "patch", "delete"]);

/** Paths of the schema that declare an operation for `M`. */
export type PathsWith<M extends HttpMethod> = {
  [P in keyof paths]: paths[P] extends { [K in M]: unknown } ? P : never;
}[keyof paths];

type Operation<P extends keyof paths, M extends HttpMethod> = paths[P] extends {
  [K in M]: infer Op;
}
  ? Op
  : never;

type SuccessStatus = 200 | 201 | 202 | 203 | 204 | 206;

type JsonContent<R> = R extends { content: { "application/json": infer B } }
  ? B
  : R extends { content?: never }
    ? undefined
    : unknown;

/** Body of the 2xx responses of an operation; `undefined` for responses without content. */
export type SuccessBody<Op> = Op extends { responses: infer Rs }
  ? { [S in keyof Rs & SuccessStatus]: JsonContent<Rs[S]> }[keyof Rs & SuccessStatus]
  : never;

type PathOption<Op> = Op extends { parameters: { path: infer Pp } }
  ? { path: Pp }
  : { path?: never };

type QueryOption<Op> = Op extends { parameters: { query?: infer Q } }
  ? [NonNullable<Q>] extends [never]
    ? { query?: never }
    : { query?: NonNullable<Q> }
  : { query?: never };

type BodyOption<Op> = Op extends { requestBody: { content: { "application/json": infer B } } }
  ? { body: B }
  : Op extends { requestBody?: { content: { "application/json": infer B } } }
    ? [B] extends [never]
      ? { body?: never }
      : { body?: B }
    : { body?: never };

interface CommonOptions {
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export type RequestOptions<Op> = PathOption<Op> & QueryOption<Op> & BodyOption<Op> & CommonOptions;

type OptionsArg<Op> =
  Record<never, never> extends RequestOptions<Op>
    ? [options?: RequestOptions<Op>]
    : [options: RequestOptions<Op>];

/** One entry of the `errors` extension of a 422 problem. */
export interface ValidationProblemItem {
  loc: (string | number)[];
  msg: string;
  type: string;
}

/** RFC 9457 problem details, including the extension members the API emits. */
export interface ProblemDetails {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  locked_until?: string;
  errors?: ValidationProblemItem[];
  [extension: string]: unknown;
}

/** A non-2xx API response. */
export class ApiError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string | undefined;
  readonly type: string;
  readonly problem: ProblemDetails;

  constructor(status: number, problem: ProblemDetails) {
    const title = problem.title ?? `HTTP ${String(status)}`;
    super(problem.detail ?? title);
    this.name = "ApiError";
    this.status = status;
    this.title = title;
    this.detail = problem.detail;
    this.type = problem.type ?? "about:blank";
    this.problem = problem;
  }

  /** `locked_until` extension of a 423 response, as an ISO 8601 string. */
  get lockedUntil(): string | undefined {
    return typeof this.problem.locked_until === "string" ? this.problem.locked_until : undefined;
  }

  /** `errors` extension of a 422 response. */
  get errors(): ValidationProblemItem[] | undefined {
    return Array.isArray(this.problem.errors) ? this.problem.errors : undefined;
  }
}

export interface ApiClientConfig {
  /** Prefix of every path; the default `""` keeps requests same-origin (`/api/v1/...`). */
  baseUrl?: string;
  fetch?: typeof fetch;
}

/** Read a cookie visible to scripts; `undefined` when it is absent or outside a browser. */
export function readCookie(name: string): string | undefined {
  if (typeof document === "undefined") {
    return undefined;
  }
  for (const pair of document.cookie.split(";")) {
    const separator = pair.indexOf("=");
    if (separator === -1 || pair.slice(0, separator).trim() !== name) {
      continue;
    }
    const raw = pair.slice(separator + 1).trim();
    try {
      return decodeURIComponent(raw);
    } catch {
      return raw;
    }
  }
  return undefined;
}

function buildUrl(
  baseUrl: string,
  template: string,
  path: Record<string, unknown> | undefined,
  query: Record<string, unknown> | undefined,
): string {
  const resolved = template.replace(/\{([^}]+)\}/g, (_match, name: string) => {
    const value = path?.[name];
    if (value === undefined || value === null) {
      throw new Error(`Missing path parameter "${name}" for ${template}`);
    }
    return encodeURIComponent(String(value));
  });
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    const values: unknown[] = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (item !== undefined && item !== null) {
        search.append(key, String(item));
      }
    }
  }
  const queryString = search.toString();
  return `${baseUrl}${resolved}${queryString ? `?${queryString}` : ""}`;
}

function isJson(contentType: string | null): boolean {
  return contentType !== null && /[/+]json\b/i.test(contentType);
}

async function readBody(response: Response): Promise<unknown> {
  if (response.status === 204 || response.status === 205) {
    return undefined;
  }
  const text = await response.text();
  if (text === "") {
    return undefined;
  }
  return isJson(response.headers.get("Content-Type")) ? (JSON.parse(text) as unknown) : text;
}

async function toApiError(response: Response): Promise<ApiError> {
  let problem: ProblemDetails = {};
  try {
    const body = await readBody(response);
    if (body !== null && typeof body === "object" && !Array.isArray(body)) {
      problem = body as ProblemDetails;
    } else if (typeof body === "string") {
      problem = { detail: body };
    }
  } catch {
    // An unparsable error body still yields an ApiError with the HTTP status.
  }
  if (problem.title === undefined && response.statusText !== "") {
    problem = { ...problem, title: response.statusText };
  }
  return new ApiError(response.status, problem);
}

export interface ApiClient {
  request<M extends HttpMethod, P extends PathsWith<M>>(
    method: M,
    path: P,
    ...options: OptionsArg<Operation<P, M>>
  ): Promise<SuccessBody<Operation<P, M>>>;
  get<P extends PathsWith<"get">>(
    path: P,
    ...options: OptionsArg<Operation<P, "get">>
  ): Promise<SuccessBody<Operation<P, "get">>>;
  post<P extends PathsWith<"post">>(
    path: P,
    ...options: OptionsArg<Operation<P, "post">>
  ): Promise<SuccessBody<Operation<P, "post">>>;
  put<P extends PathsWith<"put">>(
    path: P,
    ...options: OptionsArg<Operation<P, "put">>
  ): Promise<SuccessBody<Operation<P, "put">>>;
  patch<P extends PathsWith<"patch">>(
    path: P,
    ...options: OptionsArg<Operation<P, "patch">>
  ): Promise<SuccessBody<Operation<P, "patch">>>;
  delete<P extends PathsWith<"delete">>(
    path: P,
    ...options: OptionsArg<Operation<P, "delete">>
  ): Promise<SuccessBody<Operation<P, "delete">>>;
}

interface UntypedOptions extends CommonOptions {
  path?: Record<string, unknown>;
  query?: Record<string, unknown>;
  body?: unknown;
}

/** Create a client; requests resolve `fetch` at call time so tests can stub it. */
export function createApiClient(config: ApiClientConfig = {}): ApiClient {
  const baseUrl = (config.baseUrl ?? "").replace(/\/+$/, "");

  async function send(method: HttpMethod, template: string, options: UntypedOptions = {}) {
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json, application/problem+json");
    if (CSRF_METHODS.has(method)) {
      const token = readCookie(CSRF_COOKIE_NAME);
      if (token !== undefined && token !== "") {
        headers.set(CSRF_HEADER_NAME, token);
      }
    }
    const init: RequestInit = {
      method: method.toUpperCase(),
      credentials: "include",
      headers,
      signal: options.signal,
    };
    if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(options.body);
    }
    const url = buildUrl(baseUrl, template, options.path, options.query);
    const fetchImpl = config.fetch ?? globalThis.fetch;
    const response = await fetchImpl(url, init);
    if (!response.ok) {
      throw await toApiError(response);
    }
    return readBody(response);
  }

  // The public signatures are enforced by the ApiClient interface; at runtime every method
  // shares the untyped implementation.
  const request = (method: HttpMethod, path: string, options?: UntypedOptions) =>
    send(method, path, options);
  const bind = (method: HttpMethod) => (path: string, options?: UntypedOptions) =>
    send(method, path, options);

  return {
    request,
    get: bind("get"),
    post: bind("post"),
    put: bind("put"),
    patch: bind("patch"),
    delete: bind("delete"),
  } as unknown as ApiClient;
}

/** Default same-origin client. */
export const api: ApiClient = createApiClient();

export const apiFetch: ApiClient["request"] = (...args) => api.request(...args);
export const get: ApiClient["get"] = (...args) => api.get(...args);
export const post: ApiClient["post"] = (...args) => api.post(...args);
