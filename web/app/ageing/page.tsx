"use client";

/**
 * Ageing infrastructure — the second half of the gap analysis the problem
 * statement asks for. Coverage answers "where is nothing watching"; this
 * answers "what is about to stop watching", which is a calendar question and
 * needs a different page rather than another tab of the same numbers.
 *
 * The thresholds are on screen and editable because they are policy, not fact:
 * a report that hides what it meant by "old" cannot be taken to a budget meeting.
 */

import { useCallback, useEffect, useState } from "react";

import {
  Button,
  EmptyState,
  Field,
  Input,
  LinkButton,
  Metric,
  Notice,
  Panel,
  PageHeader,
  SectionTitle,
  SkeletonRows,
  TD,
  TH,
  THead,
  TR,
  Table,
  Toolbar,
} from "@/components/ui";
import { API, apiFetch } from "@/lib/session";

type Band = { label: string; count: number; share: number };

type DepartmentRow = {
  department_id: string;
  department_code: string;
  department_name: string;
  total: number;
  past_service_life: number;
  amc_expired: number;
  amc_expiring_soon: number;
  retention_below_policy: number;
  unknown_install_date: number;
  oldest_install_date: string | null;
};

type Report = {
  generated_for: string;
  thresholds: {
    service_life_years: number;
    amc_horizon_days: number;
    min_retention_days: number;
  };
  totals: {
    cameras: number;
    needs_attention: number;
    past_service_life: number;
    amc_expired: number;
    amc_expiring_soon: number;
    retention_below_policy: number;
    unknown_install_date: number;
  };
  bands: Band[];
  departments: DepartmentRow[];
};

const DEFAULTS = { serviceLife: 5, amcHorizon: 90, minRetention: 30 };

export default function AgeingPage() {
  const [thresholds, setThresholds] = useState(DEFAULTS);
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const query = new URLSearchParams({
    service_life_years: String(thresholds.serviceLife),
    amc_horizon_days: String(thresholds.amcHorizon),
    min_retention_days: String(thresholds.minRetention),
  }).toString();

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch(`/api/v1/lifecycle/ageing?${query}`);
      if (!response.ok) throw new Error(`Report failed (${response.status})`);
      setReport(await response.json());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Report failed");
    } finally {
      setLoading(false);
    }
  }, [query]);

  useEffect(() => {
    void load();
  }, [load]);

  const totals = report?.totals;
  // The share of the fleet nobody can plan around: an undated camera is neither
  // old nor new, and reporting it as "not old" would understate the problem.
  const undatedShare =
    totals && totals.cameras
      ? (totals.unknown_install_date / totals.cameras) * 100
      : 0;

  return (
    <div className="mx-auto max-w-[76rem] p-6">
      <PageHeader
        title="Ageing infrastructure"
        description="Cameras past their service life, out of maintenance contract, or retaining less footage than policy requires. Every threshold below is editable — replacement cycles differ by department and procurement round."
        actions={
          <LinkButton
            href={`${API}/api/v1/lifecycle/ageing.csv?${query}`}
            target="_blank"
            rel="noreferrer"
          >
            Export CSV
          </LinkButton>
        }
      />

      <Toolbar>
        <div className="w-32">
          <Field label="Service life" hint="years">
            <Input
              type="number"
              min={1}
              max={30}
              value={thresholds.serviceLife}
              onChange={(e) =>
                setThresholds((t) => ({ ...t, serviceLife: Number(e.target.value) }))
              }
            />
          </Field>
        </div>
        <div className="w-32">
          <Field label="AMC horizon" hint="days ahead">
            <Input
              type="number"
              min={0}
              max={1095}
              value={thresholds.amcHorizon}
              onChange={(e) =>
                setThresholds((t) => ({ ...t, amcHorizon: Number(e.target.value) }))
              }
            />
          </Field>
        </div>
        <div className="w-32">
          <Field label="Min retention" hint="days">
            <Input
              type="number"
              min={0}
              max={3650}
              value={thresholds.minRetention}
              onChange={(e) =>
                setThresholds((t) => ({ ...t, minRetention: Number(e.target.value) }))
              }
            />
          </Field>
        </div>
        <Button variant="subtle" onClick={() => setThresholds(DEFAULTS)}>
          Reset
        </Button>
      </Toolbar>

      {error && (
        <div className="mb-4">
          <Notice tone="error" title="Could not load the report">
            {error}
          </Notice>
        </div>
      )}

      {totals && (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Metric
              label="Needs attention"
              value={totals.needs_attention.toLocaleString()}
              foot={`of ${totals.cameras.toLocaleString()} active cameras`}
              tone={totals.needs_attention > 0 ? "warn" : "default"}
            />
            <Metric
              label="Past service life"
              value={totals.past_service_life.toLocaleString()}
              foot={`installed over ${report.thresholds.service_life_years} years ago`}
            />
            <Metric
              label="AMC expired"
              value={totals.amc_expired.toLocaleString()}
              foot={`${totals.amc_expiring_soon.toLocaleString()} expiring within ${report.thresholds.amc_horizon_days} days`}
            />
            <Metric
              label="Under-retaining"
              value={totals.retention_below_policy.toLocaleString()}
              foot={`keeping under ${report.thresholds.min_retention_days} days of footage`}
            />
          </div>

          {/* Stated rather than buried: a camera in three problem categories is
              one replacement, so the headline must not be read as a sum. */}
          <p className="mb-6 text-[length:var(--text-xs)] text-ink-faint">
            A camera counts once toward &ldquo;needs attention&rdquo; however many
            categories it falls into, so the four figures above do not sum.
          </p>

          {totals.unknown_install_date > 0 && (
            <div className="mb-6">
              <Notice tone="warn" title="Cameras with no installation date">
                <strong>{totals.unknown_install_date.toLocaleString()}</strong> of{" "}
                {totals.cameras.toLocaleString()} cameras (
                {undatedShare.toFixed(1)}%) have no recorded installation date, so
                they appear in no age band and cannot be planned around. That gap is
                itself a finding: the source systems are not sending the field.
              </Notice>
            </div>
          )}

          <div className="mb-6">
            <SectionTitle>Fleet age profile</SectionTitle>
            <Panel>
              {report.bands.every((b) => b.count === 0) ? (
                <EmptyState title="No installation dates recorded">
                  Age bands need <code>install_date</code>. Import it with a bulk
                  upload, or map it from a source field on the connector.
                </EmptyState>
              ) : (
                <ul className="flex flex-col gap-2.5">
                  {report.bands.map((band) => (
                    <li key={band.label} className="flex items-center gap-3">
                      <span className="w-28 shrink-0 text-[length:var(--text-xs)] text-ink-muted">
                        {band.label}
                      </span>
                      {/* A bar, not a chart library: one dimension of data does
                          not justify shipping a rendering engine. */}
                      <span className="h-4 min-w-px flex-1 overflow-hidden rounded-[3px] bg-sunken">
                        <span
                          className="block h-full rounded-[3px] transition-[width] duration-[var(--duration)] ease-[var(--ease)]"
                          style={{
                            width: `${band.share}%`,
                            background:
                              band.label === "Over 8 years" ||
                              band.label === "5-8 years"
                                ? "var(--state-maintenance-ink)"
                                : "var(--brand)",
                          }}
                        />
                      </span>
                      <span className="w-24 shrink-0 text-right text-[length:var(--text-xs)] tabular-nums text-ink-muted">
                        {band.count.toLocaleString()} · {band.share.toFixed(1)}%
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        </>
      )}

      <SectionTitle>By department</SectionTitle>
      <Table>
        <THead>
          <TR>
            <TH>Department</TH>
            <TH align="right">Cameras</TH>
            <TH align="right">Past service life</TH>
            <TH align="right">AMC expired</TH>
            <TH align="right">Expiring soon</TH>
            <TH align="right">Under-retaining</TH>
            <TH align="right">No date</TH>
            <TH>Oldest</TH>
          </TR>
        </THead>
        <tbody>
          {loading && <SkeletonRows rows={5} cols={8} />}
          {!loading &&
            report?.departments.map((row) => (
              <TR key={row.department_id}>
                <TD>
                  <span className="font-medium text-ink">{row.department_code}</span>
                  <span className="ml-2 text-ink-muted">{row.department_name}</span>
                </TD>
                <TD align="right">{row.total.toLocaleString()}</TD>
                <TD align="right">
                  <Count value={row.past_service_life} />
                </TD>
                <TD align="right">
                  <Count value={row.amc_expired} />
                </TD>
                <TD align="right">
                  <Count value={row.amc_expiring_soon} muted />
                </TD>
                <TD align="right">
                  <Count value={row.retention_below_policy} />
                </TD>
                <TD align="right">
                  <Count value={row.unknown_install_date} muted />
                </TD>
                <TD>
                  <span className="text-[length:var(--text-xs)] tabular-nums text-ink-muted">
                    {row.oldest_install_date ?? "—"}
                  </span>
                </TD>
              </TR>
            ))}
        </tbody>
      </Table>

      {!loading && report?.departments.length === 0 && (
        <Panel className="mt-3">
          <EmptyState title="No active cameras to report on">
            Onboard a department first — the ageing report reads installation dates
            and contract expiry from the registry.
          </EmptyState>
        </Panel>
      )}

      {report && (
        <p className="mt-4 text-[length:var(--text-xs)] text-ink-faint">
          Generated for {report.generated_for}. Service life{" "}
          {report.thresholds.service_life_years} years · AMC horizon{" "}
          {report.thresholds.amc_horizon_days} days · minimum retention{" "}
          {report.thresholds.min_retention_days} days.
        </p>
      )}
    </div>
  );
}

/** Zero is the good outcome here, so it recedes rather than sitting in bold. */
function Count({ value, muted }: { value: number; muted?: boolean }) {
  if (value === 0) {
    return <span className="tabular-nums text-ink-faint">0</span>;
  }
  return (
    <span
      className="font-medium tabular-nums"
      style={{ color: muted ? "var(--ink)" : "var(--state-maintenance-ink)" }}
    >
      {value.toLocaleString()}
    </span>
  );
}
