import LibraryDocApp, { type LibraryDocParams } from "@/components/library/LibraryDocApp";

export const dynamic = "force-dynamic";

// One GR of the library: its pages (with a provision or a cited quote marked), its outline, the OCR lines for review
// and correction, and its metadata. Deep links: ?prov=<provision>&page=<n>&q=<quote>, ?tab=lines.
export default async function LibraryDocPage({ params, searchParams }: {
  params: Promise<{ id: string }>; searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const [{ id }, sp] = await Promise.all([params, searchParams]);
  const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
  const p: LibraryDocParams = { prov: one(sp.prov), page: one(sp.page), q: one(sp.q), tab: one(sp.tab) };
  return <LibraryDocApp docId={id} params={p} />;
}
