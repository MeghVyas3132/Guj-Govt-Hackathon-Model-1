"use client";

import { useEffect, useState } from "react";

import { API, apiJson, getToken, useCurrentUser } from "@/lib/session";

type Department = { id: string; code: string; name: string };

type RowResult = {
  row_number: number | null;
  external_camera_id: string | null;
  outcome: string;
  errors: { code: string; message: string; field: string | null }[];
  warnings: string[];
};

type Report = {
  total: number;
  created: number;
  updated: number;
  skipped: number;
  failed: number;
  rows: RowResult[];
};

const OUTCOME_STYLE: Record<string, string> = {
  created: "bg-green-100 text-green-800",
  updated: "bg-blue-100 text-blue-800",
  skipped: "bg-slate-100 text-slate-600",
  failed: "bg-red-100 text-red-800",
};

export default function OnboardingPage() {
  const user = useCurrentUser();
  const [departments, setDepartments] = useState<Department[]>([]);
  const [departmentId, setDepartmentId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Report | null>(null);
  const [result, setResult] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    apiJson<Department[]>("/api/v1/departments")
      .then((data) => {
        if (!active) return;
        setDepartments(data);
        const own = user?.department_id;
        setDepartmentId(own ?? data[0]?.id ?? "");
      })
      .catch(() => active && setError("Could not load departments."));
    return () => {
      active = false;
    };
  }, [user?.department_id]);

  async function send(path: "preview" | "import") {
    if (!file || !departmentId) return;
    setBusy(true);
    setError(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const token = getToken();
      const response = await fetch(
        `${API}/api/v1/onboarding/${path}?department_id=${departmentId}`,
        {
          method: "POST",
          body,
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        },
      );
      const payload = await response.json();
      if (!response.ok) {
        setError(
          typeof payload.detail === "string" ? payload.detail : "Upload rejected.",
        );
        return;
      }
      if (path === "preview") {
        setPreview(payload);
        setResult(null);
      } else {
        setResult(payload);
      }
    } catch {
      setError("Could not reach the registry.");
    } finally {
      setBusy(false);
    }
  }

  const canWrite = user?.scopes.includes("cameras:write") ?? false;
  const report = result ?? preview;
  const problems = report?.rows.filter((r) => r.errors.length || r.warnings.length) ?? [];

  return (
    <main className="mx-auto max-w-4xl p-8">
      <h1 className="mb-1 text-2xl font-semibold">Bulk onboarding</h1>
      <p className="mb-6 text-sm text-slate-500">
        Upload a department&rsquo;s camera list. Validate first to see exactly which rows
        would fail and why — nothing is written until you import.
      </p>

      {!canWrite && (
        <p className="mb-6 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          Your role cannot onboard cameras. You can still validate a file.
        </p>
      )}

      <div className="mb-6 rounded border p-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-xs font-semibold uppercase text-slate-500">
              Department
            </span>
            <select
              className="w-full rounded border px-3 py-2 text-sm"
              value={departmentId}
              onChange={(e) => setDepartmentId(e.target.value)}
            >
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.code} — {d.name}
                </option>
              ))}
            </select>
            <span className="mt-1 block text-xs text-slate-400">
              Its field mapping decides how your columns are read.
            </span>
          </label>

          <label className="block">
            <span className="mb-1 block text-xs font-semibold uppercase text-slate-500">
              CSV file
            </span>
            <input
              type="file"
              accept=".csv,text/csv"
              className="w-full text-sm"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setPreview(null);
                setResult(null);
              }}
            />
          </label>
        </div>

        <div className="mt-4 flex gap-3">
          <button
            onClick={() => send("preview")}
            disabled={!file || busy}
            className="rounded border px-4 py-2 text-sm font-medium disabled:opacity-40"
          >
            {busy ? "Working…" : "1 · Validate"}
          </button>
          <button
            onClick={() => send("import")}
            disabled={!preview || busy || !canWrite}
            className="rounded bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-40"
          >
            2 · Import{preview ? ` ${preview.total - preview.failed} rows` : ""}
          </button>
        </div>
      </div>

      {error && (
        <p className="mb-6 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}

      {report && (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <h2 className="text-sm font-semibold uppercase text-slate-500">
              {result ? "Imported" : "Validation result — nothing written yet"}
            </h2>
            {result && (
              <span className="rounded bg-green-100 px-2 py-0.5 text-xs text-green-800">
                committed
              </span>
            )}
          </div>

          <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-5">
            <Stat label="Rows" value={report.total} />
            <Stat label="Created" value={report.created} tone="text-green-700" />
            <Stat label="Updated" value={report.updated} tone="text-blue-700" />
            <Stat label="Unchanged" value={report.skipped} />
            <Stat label="Failed" value={report.failed} tone="text-red-700" />
          </div>

          {problems.length > 0 && (
            <div className="overflow-x-auto rounded border">
              <table className="w-full text-sm">
                <thead className="border-b bg-slate-50 text-left text-xs uppercase text-slate-500">
                  <tr>
                    <th className="px-3 py-2">Row</th>
                    <th className="px-3 py-2">Camera</th>
                    <th className="px-3 py-2">Outcome</th>
                    <th className="px-3 py-2">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {problems.map((row, index) => (
                    <tr key={index} className="border-b align-top last:border-0">
                      <td className="px-3 py-2 text-slate-500">{row.row_number ?? "—"}</td>
                      <td className="px-3 py-2 font-mono text-xs">
                        {row.external_camera_id ?? "—"}
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={`rounded px-2 py-0.5 text-xs ${
                            OUTCOME_STYLE[row.outcome] ?? OUTCOME_STYLE.skipped
                          }`}
                        >
                          {row.outcome}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        {row.errors.map((e) => (
                          <p key={e.code} className="text-red-700">
                            <span className="font-mono text-xs">{e.code}</span>{" "}
                            {e.message}
                          </p>
                        ))}
                        {row.warnings.map((w, i) => (
                          <p key={i} className="text-amber-700">
                            {w}
                          </p>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {problems.length === 0 && (
            <p className="rounded border border-green-200 bg-green-50 p-3 text-sm text-green-800">
              Every row is valid.
            </p>
          )}
        </>
      )}
    </main>
  );
}

function Stat({
  label,
  value,
  tone = "text-slate-900",
}: {
  label: string;
  value: number;
  tone?: string;
}) {
  return (
    <div className="rounded border p-3">
      <p className="text-xs uppercase text-slate-500">{label}</p>
      <p className={`text-2xl font-semibold tabular-nums ${tone}`}>{value}</p>
    </div>
  );
}
