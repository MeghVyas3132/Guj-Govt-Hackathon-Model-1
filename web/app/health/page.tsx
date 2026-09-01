import { PageHeader } from "@/components/ui";
import { OfflineTable } from "@/components/OfflineTable";

export const metadata = { title: "Camera health — Sentinel CCTV Registry" };

export default function HealthPage() {
  return (
    <main className="mx-auto max-w-[76rem] p-6">
      <PageHeader
        title="Camera health"
        description="Which cameras are not watching, and how long they have been down. Sorted by the longest outage, because that is the one somebody has stopped noticing."
      />
      <OfflineTable />
    </main>
  );
}
