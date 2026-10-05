"use client";
// Browser-side API access through the Next.js proxy (/api/rci/*).
export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function parse(res: Response): Promise<unknown> {
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") ?? "";
  const body = ct.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = typeof body === "object" && body && "detail" in body ? (body as { detail: unknown }).detail : body;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

export const pageImageUrl = (caseId: string, seq: number, masked: boolean, w = 1400) =>
  `/api/rci/cases/${caseId}/pages/${seq}/image?masked=${masked}&w=${w}`;

export async function get<T>(path: string): Promise<T> {
  return (await parse(await fetch(`/api/rci${path}`, { cache: "no-store" }))) as T;
}

// Uploads go straight to the API when NEXT_PUBLIC_RCI_API_URL is set: a hosted proxy (Vercel) caps request bodies
// at 4.5 MB, below a scanned docket. The API allows this origin via RCI_CORS_ORIGINS.
const UPLOAD_BASE = process.env.NEXT_PUBLIC_RCI_API_URL || "/api/rci";

export async function send<T>(method: "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method };
  let base = "/api/rci";
  if (body instanceof FormData) {
    init.body = body;
    base = UPLOAD_BASE;
  } else if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "content-type": "application/json" };
  }
  return (await parse(await fetch(`${base}${path}`, init))) as T;
}
