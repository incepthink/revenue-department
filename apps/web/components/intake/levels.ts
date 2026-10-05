import type { IntakeLevel } from "@/lib/api";

// Finding levels as the document check and the document viewer show them, in panel order.
export const LEVEL: Record<IntakeLevel, { label: string; icon: string }> = {
  problem: { label: "Problems to resolve", icon: "✕" },
  warning: { label: "Points for the officer", icon: "!" },
  info: { label: "For information", icon: "i" },
  unchecked: { label: "Could not be checked", icon: "?" },
  ok: { label: "Passed", icon: "✓" },
};

export const LEVEL_ORDER: IntakeLevel[] = ["problem", "warning", "info", "unchecked", "ok"];
