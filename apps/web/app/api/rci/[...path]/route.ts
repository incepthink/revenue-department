import { NextRequest } from "next/server";
import { API_URL } from "@/lib/api";

// Same-origin proxy to the FastAPI service (no login: the API acts as the single operator user), streams bodies
// through (JSON, multipart uploads, page images). Only a fixed set of response headers is passed back.
const PASS_BACK = ["content-type", "content-disposition", "cache-control", "x-rci-mask"];
// Case analysis and drafting can run LLM calls for minutes; raise the hosted function limit (Vercel).
export const maxDuration = 300;

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const url = `${API_URL}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  const ct = req.headers.get("content-type");
  if (ct) headers.set("content-type", ct);
  const fwd = req.headers.get("x-forwarded-for");
  if (fwd) headers.set("x-forwarded-for", fwd);
  const init: RequestInit = { method: req.method, headers, cache: "no-store" };
  if (req.method !== "GET" && req.method !== "HEAD") init.body = await req.arrayBuffer();
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    return Response.json({ detail: "API unavailable — start it with `make api`" }, { status: 502 });
  }
  const out = new Headers();
  for (const h of PASS_BACK) {
    const v = res.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(res.body, { status: res.status, headers: out });
}

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
export const DELETE = forward;
