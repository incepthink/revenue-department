// Server-side API access. There is no login: the API treats tokenless requests as the single operator user.
import { API_URL } from "@/lib/api";

export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: ${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}
