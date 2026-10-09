import SearchApp from "@/components/library/SearchApp";

export const dynamic = "force-dynamic";

// GR search: one box (Marathi or English), a cited answer checked against the GR text, and the passages it rests on.
export default async function LibrarySearchPage({ searchParams }: { searchParams: Promise<{ q?: string | string[] }> }) {
  const sp = await searchParams;
  const q = Array.isArray(sp.q) ? sp.q[0] : sp.q;
  return <SearchApp initialQuery={q ?? ""} />;
}
