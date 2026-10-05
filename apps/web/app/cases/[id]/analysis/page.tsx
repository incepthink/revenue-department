import AnalysisApp from "@/components/analysis/AnalysisApp";

export const dynamic = "force-dynamic";

export default async function AnalysisPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <AnalysisApp caseId={id} canRun />;
}
