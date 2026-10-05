import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

// The old text-extraction screen is the Text tab of the documents screen now.
export default async function ReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/cases/${id}/documents?tab=text`);
}
