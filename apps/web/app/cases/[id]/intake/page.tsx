import { redirect } from "next/navigation";
import IntakeApp from "@/components/intake/IntakeApp";
import type { CaseSummary } from "@/lib/api";
import { apiGet } from "@/lib/server";

export const dynamic = "force-dynamic";

// Where "New case" lands (?new=1): progress while each document is read and checked, then the pages with the findings
// drawn on them. Opened later, the full report: the verdict and the feedback.
// A Collector's report uploaded through "Prepare Noting" has its own page.
export default async function IntakePage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: Promise<{ new?: string | string[] }>;
}) {
  const [{ id }, sp] = await Promise.all([params, searchParams]);
  const c = await apiGet<CaseSummary>(`/cases/${id}`).catch(() => null);
  if (c?.kind === "noting") redirect(`/notings/${id}`);
  return <IntakeApp caseId={id} toPages={sp.new !== undefined} />;
}
