import DocumentsApp, { type DocumentsParams } from "@/components/documents/DocumentsApp";

export const dynamic = "force-dynamic";

// The case's documents: the pages with the document check's findings drawn on them, or the extracted text.
export default async function DocumentsPage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: Promise<DocumentsParams>;
}) {
  const [{ id }, sp] = await Promise.all([params, searchParams]);
  const one = (v: unknown) => (Array.isArray(v) ? v[0] : v) as string | undefined;
  return <DocumentsApp caseId={id} params={{ f: one(sp.f), a: one(sp.a), fact: one(sp.fact), seq: one(sp.seq), tab: one(sp.tab) }} />;
}
