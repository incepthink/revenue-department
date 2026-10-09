import TimelineApp from "@/components/library/TimelineApp";

export const dynamic = "force-dynamic";

// Rules in time: the GRs of a section by date with the links between them, for the operator to confirm.
export default async function LibraryTimelinePage({ searchParams }: { searchParams: Promise<{ section?: string }> }) {
  const sp = await searchParams;
  return <TimelineApp initialSection={(sp.section ?? "E").toUpperCase()} />;
}
