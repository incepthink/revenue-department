import IntakeApp from "@/components/intake/IntakeApp";

export const dynamic = "force-dynamic";

// Where "New case" lands: progress while each document is read and checked, then the verdict and the feedback.
export default async function IntakePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <IntakeApp caseId={id} />;
}
