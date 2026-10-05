import ClausesApp from "@/components/admin/ClausesApp";

export const dynamic = "force-dynamic";

export default function ClausesPage() {
  return <ClausesApp isAdmin canEdit />;
}
