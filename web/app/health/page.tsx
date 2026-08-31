import { OfflineTable } from "@/components/OfflineTable";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const metadata = {
  title: "Camera health — Sentinel CCTV Registry",
};

// HealthSummary in app/schemas/health.py.
type Summary = {
  total: number;
  online: number;
  offline: number;
  unknown: number;
  maintenance: number;
  offline_over_24h: number;
  offline_over_7d: number;
};

async function getSummary(): Promise<Summary | null> {
  try {
    // cache: "no-store" is what keeps this route out of the build-time prerender:
    // in Next 16 an uncached fetch is refetched on every request even when no
    // request-time API is used, so the tiles are the counts at page load and not
    // the counts at `next build`.
    const response = await fetch(`${API}/api/v1/health/summary`, { cache: "no-store" });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

export default async function HealthPage() {
  const summary = await getSummary();

  const tiles: [string, number, string][] = summary
    ? [
        ["Total", summary.total, "text-slate-900"],
        ["Online", summary.online, "text-green-600"],
        ["Offline", summary.offline, "text-red-600"],
        ["Maintenance", summary.maintenance, "text-amber-600"],
        ["Down > 24h", summary.offline_over_24h, "text-red-700"],
        ["Down > 7d", summary.offline_over_7d, "text-red-800"],
      ]
    : [];

  return (
    <main className="mx-auto w-full max-w-6xl p-8">
      <h1 className="mb-6 text-2xl font-semibold">Camera health</h1>

      {summary === null ? (
        <p
          data-testid="summary-unreachable"
          className="mb-8 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800"
        >
          Registry API unreachable.
        </p>
      ) : (
        <div
          data-testid="summary-tiles"
          className="mb-8 grid grid-cols-2 gap-4 md:grid-cols-6"
        >
          {tiles.map(([label, value, colour]) => (
            <div key={label} className="rounded-lg border p-4">
              <p className="text-xs uppercase text-slate-500">{label}</p>
              <p className={`text-2xl font-semibold ${colour}`}>{value}</p>
            </div>
          ))}
        </div>
      )}

      <h2 className="mb-3 text-lg font-medium">Offline, longest first</h2>
      <OfflineTable />
    </main>
  );
}
