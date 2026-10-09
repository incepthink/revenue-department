import NotingApp from "@/components/noting/NotingApp";

export const dynamic = "force-dynamic";

// Where "Prepare Noting" lands: the report's pages, the letter's facts to review, then the Noting to download.
export default async function NotingPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <NotingApp caseId={id} />;
}
