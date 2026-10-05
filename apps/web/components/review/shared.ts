"use client";
import { createContext, useContext } from "react";
import type { ReviewView } from "@/lib/api";

export type Cite = { page_seq: number; text: string };

export type ReviewCtx = {
  caseId: string;
  view: ReviewView;
  readOnly: boolean;
  busy: boolean;
  run: (ops: Record<string, unknown>[]) => Promise<boolean>;
  jump: (pageSeq: number, highlight?: string | null) => void;
  page: number;
  cite: Cite | null;
  setCite: (c: Cite | null) => void;
  notify: (kind: "error" | "success" | "notice", text: string) => void;
  reload: () => Promise<void>;
};

export const Ctx = createContext<ReviewCtx | null>(null);

export function useReview(): ReviewCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useReview outside ReviewApp");
  return c;
}

export function confClass(c: number | null | undefined): "high" | "mid" | "low" | "none" {
  if (c === null || c === undefined) return "none";
  if (c >= 0.9) return "high";
  if (c >= 0.7) return "mid";
  return "low";
}

export const norm = (s: string) => s.replace(/\s+/g, " ").trim();

export function range(a: number, b: number): number[] {
  return Array.from({ length: b - a + 1 }, (_, i) => a + i);
}
