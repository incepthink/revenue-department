import DraftsApp from "@/components/drafts/DraftsApp";
import type { CaseDetail } from "@/lib/api";
import { apiGet } from "@/lib/server";

export const dynamic = "force-dynamic";

export default async function DraftsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const c = await apiGet<CaseDetail>(`/cases/${id}`);
  return <DraftsApp caseId={id} caseRef={c.case_ref} />;
}
