import BacktestRunView from "@/components/backtest/BacktestRun";

export const dynamic = "force-dynamic";

export default async function BacktestRunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <BacktestRunView runId={id} canLabel />;
}
