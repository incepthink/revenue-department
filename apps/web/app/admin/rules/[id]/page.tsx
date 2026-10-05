import RuleSetEditor from "@/components/admin/RuleSetEditor";

export const dynamic = "force-dynamic";

export default async function RuleSetPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RuleSetEditor id={id} />;
}
