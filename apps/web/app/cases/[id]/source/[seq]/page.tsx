import SourcePage, { type PageInfo } from "@/components/analysis/SourcePage";
import type { CaseSummary } from "@/lib/api";
import { apiGet } from "@/lib/server";

export const dynamic = "force-dynamic";

// A cited page: PII-masked image (when one was rendered) and the redacted text, with the cited snippet highlighted.
export default async function SourceRoute({ params, searchParams }: {
  params: Promise<{ id: string; seq: string }>; searchParams: Promise<{ q?: string }>;
}) {
  const [{ id, seq }, { q }] = await Promise.all([params, searchParams]);
  const [page, c] = await Promise.all([apiGet<PageInfo>(`/cases/${id}/pages/${seq}`), apiGet<CaseSummary>(`/cases/${id}`)]);
  return <SourcePage caseId={id} caseRef={c.case_ref} society={c.society_name} page={page} q={q ?? null} />;
}
