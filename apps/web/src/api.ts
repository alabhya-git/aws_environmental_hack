// One client for both workspaces. The demo keeps its own journal in this
// browser and sends it with every request; the server stores nothing for it.
// The pilot workspace is persisted on the server and needs a signed-in operator.
import type { components } from "./api-schema";
import { currentToken, devUser, signOut } from "./auth";

export type Mode = "demo" | "pilot";

// Request types are generated from the API's OpenAPI schema (npm run gen:api),
// so a renamed or mistyped field fails the type-check instead of failing at the counter.
type Schemas = components["schemas"];
export type OpArgs = { [K in keyof Schemas as K extends `Pilot_${infer Op}` ? Op : never]: Schemas[K] extends { args: infer A } ? A : never };
export type OpName = keyof OpArgs;

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number, public offline = false) {
    super(message);
  }
}

const SANDBOX_KEY = "proofplate.demo.sandbox.v1";

export function readSandbox(): unknown[] {
  try {
    return JSON.parse(localStorage.getItem(SANDBOX_KEY) || "[]");
  } catch {
    return [];
  }
}

export function sandboxSize(): number {
  return readSandbox().length;
}

export function resetDemo(): void {
  // Clears only this browser's demo journal. Pilot records live elsewhere and
  // are not reachable from here.
  try {
    localStorage.removeItem(SANDBOX_KEY);
  } catch {
    /* storage unavailable: nothing to clear */
  }
}

export interface CallMeta {
  requestId: string | null;
  ms: number | null;
}
export let lastCall: CallMeta = { requestId: null, ms: null };

async function parse(res: Response): Promise<any> {
  const text = await res.text();
  try {
    return text ? JSON.parse(text) : {};
  } catch {
    return { message: text.slice(0, 200) };
  }
}

export async function call<O extends OpName>(mode: Mode, op: O, args: OpArgs[O]): Promise<any> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  let body: { args: unknown; sandbox?: unknown[] } = { args };
  if (mode === "demo") {
    body = { args, sandbox: readSandbox() };
  } else {
    const token = await currentToken();
    const dev = devUser();
    if (token) headers["authorization"] = `Bearer ${token}`;
    else if (dev) headers["x-dev-user"] = dev;
    else throw new ApiError("UNAUTHENTICATED", "Sign in to use the pilot workspace.", 401);
  }
  let res: Response;
  const t0 = performance.now();
  try {
    res = await fetch(`/api/${mode}/${op}`, { method: "POST", headers, body: JSON.stringify(body) });
  } catch {
    throw new ApiError("OFFLINE", "No connection to the server. Nothing was saved.", 0, true);
  }
  lastCall = { requestId: res.headers.get("x-request-id"), ms: Math.round(performance.now() - t0) };
  const data = await parse(res);
  if (!res.ok) {
    if (res.status === 401 && mode === "pilot") signOut();
    const err = data.error || {};
    throw new ApiError(
      err.code || (res.status === 401 ? "UNAUTHENTICATED" : res.status === 429 ? "THROTTLED" : "HTTP_" + res.status),
      err.message || (res.status === 401 ? "Your session has expired. Sign in again." : res.status === 429 ? "Too many requests. Wait a moment and retry." : data.message || "The server could not complete this."),
      res.status,
    );
  }
  if (mode === "demo" && Array.isArray(data.sandbox_append) && data.sandbox_append.length) {
    try {
      localStorage.setItem(SANDBOX_KEY, JSON.stringify([...readSandbox(), ...data.sandbox_append]));
    } catch {
      throw new ApiError("STORAGE", "This browser would not store the demo sandbox, so the action was not kept.", 0);
    }
  }
  return data.result;
}

export async function getJson<T = any>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path);
  } catch {
    throw new ApiError("OFFLINE", "No connection to the server.", 0, true);
  }
  const data = await parse(res);
  if (!res.ok) throw new ApiError(data.error?.code || "HTTP_" + res.status, data.error?.message || "Request failed.", res.status);
  return data as T;
}

export function newEventId(): string {
  const a = new Uint8Array(16);
  crypto.getRandomValues(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function download(filename: string, content: string, type = "text/plain"): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
