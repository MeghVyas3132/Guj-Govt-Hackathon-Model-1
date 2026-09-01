"use client";

import { useEffect, useState } from "react";

import { ApiError, apiFetch, apiJson, postJson } from "@/lib/session";

type Dimension = { dimension: string; total: number; active: number };
type Term = {
  code: string;
  label: string;
  is_active: boolean;
  is_fallback: boolean;
  coverage_range_m: number | null;
  coverage_fov_deg: number | null;
  is_omnidirectional: boolean | null;
};
type Boundary = { id: string; name: string };
type Alias = { id: string; alias: string; source: string };
type ApiKey = {
  id: string;
  name: string;
  key_prefix: string;
  scopes: string[];
  revoked_at: string | null;
};
type Department = { id: string; code: string };
type AuditEntry = {
  id: string;
  action: string;
  entity_type: string;
  actor_label: string | null;
  at: string;
};

const TABS = ["Vocabulary", "Place aliases", "API keys", "Audit"] as const;
type Tab = (typeof TABS)[number];

export default function AdminPage() {
  const [tab, setTab] = useState<Tab>("Vocabulary");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  return (
    <main className="mx-auto max-w-[64rem] p-6">
      <h1 className="mb-1 text-[length:var(--text-xl)] font-semibold text-ink">Administration</h1>
      <p className="mb-6 text-[length:var(--text-sm)] text-ink-muted">
        The registry&rsquo;s configuration. Everything here is data — adding a camera
        type or a place name takes effect immediately, with no deploy.
      </p>

      <div className="mb-6 flex gap-1 border-b border-line">
        {TABS.map((name) => (
          <button
            key={name}
            onClick={() => {
              setTab(name);
              setError(null);
              setNotice(null);
            }}
            className={`px-3 py-2 text-sm ${
              tab === name
                ? "border-b-2 [border-color:var(--brand)] font-medium [color:var(--brand)]"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            {name}
          </button>
        ))}
      </div>

      {error && (
        <p className="mb-4 rounded-[6px] border p-3 text-[length:var(--text-sm)] [border-color:color-mix(in_oklch,var(--state-offline-ink)_30%,transparent)] [background:var(--state-offline-bg)] [color:var(--state-offline-ink)]">
          {error}
        </p>
      )}
      {notice && (
        <p className="mb-4 rounded-[6px] border p-3 text-[length:var(--text-sm)] [border-color:color-mix(in_oklch,var(--state-online-ink)_30%,transparent)] [background:var(--state-online-bg)] [color:var(--state-online-ink)]">
          {notice}
        </p>
      )}

      {tab === "Vocabulary" && <Vocabulary onError={setError} onNotice={setNotice} />}
      {tab === "Place aliases" && <Aliases onError={setError} onNotice={setNotice} />}
      {tab === "API keys" && <Keys onError={setError} onNotice={setNotice} />}
      {tab === "Audit" && <Audit onError={setError} />}
    </main>
  );
}

type Handlers = {
  onError: (message: string | null) => void;
  onNotice: (message: string | null) => void;
};

function Vocabulary({ onError, onNotice }: Handlers) {
  const [dimensions, setDimensions] = useState<Dimension[]>([]);
  const [selected, setSelected] = useState("camera_type");
  const [terms, setTerms] = useState<Term[]>([]);
  const [reload, setReload] = useState(0);
  const [form, setForm] = useState({ code: "", label: "", range: "", fov: "", omni: false });

  useEffect(() => {
    let active = true;
    apiJson<Dimension[]>("/api/v1/vocabulary")
      .then((d) => active && setDimensions(d))
      .catch((e) => active && onError(e.message));
    return () => {
      active = false;
    };
  }, [onError]);

  useEffect(() => {
    let active = true;
    apiJson<Term[]>(`/api/v1/vocabulary/${selected}?include_inactive=true`)
      .then((t) => active && setTerms(t))
      .catch((e) => active && onError(e.message));
    return () => {
      active = false;
    };
  }, [selected, reload, onError]);

  async function addTerm(event: React.FormEvent) {
    event.preventDefault();
    onError(null);
    try {
      await postJson(`/api/v1/vocabulary/${selected}`, {
        code: form.code,
        label: form.label,
        coverage_range_m: form.range ? Number(form.range) : undefined,
        coverage_fov_deg: form.fov ? Number(form.fov) : undefined,
        is_omnidirectional: form.omni || undefined,
      });
      onNotice(`Added ${selected}:${form.code}. It is usable immediately.`);
      setForm({ code: "", label: "", range: "", fov: "", omni: false });
      setReload((n) => n + 1);
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not add the term.");
    }
  }

  async function toggle(term: Term) {
    onError(null);
    try {
      await apiFetch(`/api/v1/vocabulary/${selected}/${term.code}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ is_active: !term.is_active }),
      }).then(async (r) => {
        if (!r.ok) throw new ApiError(r.status, (await r.json()).detail);
      });
      setReload((n) => n + 1);
    } catch (e) {
      onError(e instanceof ApiError ? String(e.message) : "Could not update the term.");
    }
  }

  const isCameraType = selected === "camera_type";

  return (
    <>
      <div className="mb-4 flex flex-wrap gap-1">
        {dimensions.map((d) => (
          <button
            key={d.dimension}
            onClick={() => setSelected(d.dimension)}
            className={`rounded px-2 py-1 text-xs ${
              selected === d.dimension
                ? "bg-slate-900 text-white"
                : "bg-sunken text-ink"
            }`}
          >
            {d.dimension} ({d.active})
          </button>
        ))}
      </div>

      <div className="mb-6 overflow-x-auto rounded-[6px] border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className="border-b border-line bg-sunken text-left text-[length:var(--text-2xs)] font-semibold uppercase tracking-[0.04em] text-ink-faint">
            <tr>
              <th className="px-3 py-2">Code</th>
              <th className="px-3 py-2">Label</th>
              {isCameraType && <th className="px-3 py-2">Coverage</th>}
              <th className="px-3 py-2">State</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {terms.map((term) => (
              <tr key={term.code} className="border-b border-line last:border-0">
                <td className="px-3 py-2 font-mono text-xs">{term.code}</td>
                <td className="px-3 py-2">{term.label}</td>
                {isCameraType && (
                  <td className="px-3 py-2 text-[length:var(--text-xs)] text-ink-muted">
                    {term.is_omnidirectional
                      ? `${term.coverage_range_m ?? "?"}m circle`
                      : `${term.coverage_range_m ?? "?"}m · ${term.coverage_fov_deg ?? "?"}° wedge`}
                  </td>
                )}
                <td className="px-3 py-2">
                  {term.is_fallback && (
                    <span className="mr-1 rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-800">
                      fallback
                    </span>
                  )}
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs ${
                      term.is_active
                        ? "bg-green-100 text-green-800"
                        : "bg-sunken text-ink-muted"
                    }`}
                  >
                    {term.is_active ? "active" : "retired"}
                  </span>
                </td>
                <td className="px-3 py-2 text-right">
                  {!term.is_fallback && (
                    <button
                      onClick={() => toggle(term)}
                      className="text-[length:var(--text-xs)] text-ink-muted underline-offset-2 hover:underline"
                    >
                      {term.is_active ? "Retire" : "Restore"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <form onSubmit={addTerm} className="rounded-[6px] border border-line bg-surface p-4">
        <h3 className="mb-3 text-sm font-semibold">Add a {selected} term</h3>
        <div className="flex flex-wrap items-end gap-3">
          <Input label="Code" value={form.code} onChange={(v) => setForm({ ...form, code: v })} required />
          <Input label="Label" value={form.label} onChange={(v) => setForm({ ...form, label: v })} required />
          {isCameraType && (
            <>
              <Input label="Range m" value={form.range} onChange={(v) => setForm({ ...form, range: v })} />
              <Input label="FOV °" value={form.fov} onChange={(v) => setForm({ ...form, fov: v })} />
              <label className="flex items-center gap-2 pb-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.omni}
                  onChange={(e) => setForm({ ...form, omni: e.target.checked })}
                />
                Omnidirectional
              </label>
            </>
          )}
          <button className="inline-flex h-8 items-center rounded-[4px] bg-[var(--brand)] px-3 text-[length:var(--text-sm)] font-medium text-white transition-colors duration-[var(--duration)] hover:bg-[var(--brand-hover)]">Add</button>
        </div>
        {isCameraType && (
          <p className="mt-2 text-[length:var(--text-xs)] text-ink-faint">
            The coverage fields feed the gap analysis directly, so a new type is
            modelled correctly straight away.
          </p>
        )}
      </form>
    </>
  );
}

function Aliases({ onError, onNotice }: Handlers) {
  const [boundaries, setBoundaries] = useState<Boundary[]>([]);
  const [boundaryId, setBoundaryId] = useState("");
  const [aliases, setAliases] = useState<Alias[]>([]);
  const [alias, setAlias] = useState("");
  const [source, setSource] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    apiJson<Boundary[]>("/api/v1/boundaries?level=district")
      .then((b) => {
        if (!active) return;
        setBoundaries(b);
        setBoundaryId((current) => current || b[0]?.id || "");
      })
      .catch((e) => active && onError(e.message));
    return () => {
      active = false;
    };
  }, [onError]);

  useEffect(() => {
    if (!boundaryId) return;
    let active = true;
    apiJson<Alias[]>(`/api/v1/boundaries/${boundaryId}/aliases`)
      .then((a) => active && setAliases(a))
      .catch((e) => active && onError(e.message));
    return () => {
      active = false;
    };
  }, [boundaryId, reload, onError]);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    onError(null);
    try {
      await postJson(`/api/v1/boundaries/${boundaryId}/aliases`, {
        alias,
        source: source || "manual",
      });
      onNotice(`"${alias}" now resolves. The geocoder picks it up immediately.`);
      setAlias("");
      setSource("");
      setReload((n) => n + 1);
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not add the alias.");
    }
  }

  return (
    <>
      <p className="mb-4 text-[length:var(--text-sm)] text-ink-muted">
        Sources that give a place name instead of coordinates are resolved through
        these. Recording how each mapping was established keeps a lookup distinct
        from a guess.
      </p>

      <label className="mb-4 block max-w-xs">
        <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
          District
        </span>
        <select
          className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
          value={boundaryId}
          onChange={(e) => setBoundaryId(e.target.value)}
        >
          {boundaries.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
      </label>

      <div className="mb-6 overflow-x-auto rounded-[6px] border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className="border-b border-line bg-sunken text-left text-[length:var(--text-2xs)] font-semibold uppercase tracking-[0.04em] text-ink-faint">
            <tr>
              <th className="px-3 py-2">Alias</th>
              <th className="px-3 py-2">How it was established</th>
            </tr>
          </thead>
          <tbody>
            {aliases.map((a) => (
              <tr key={a.id} className="border-b border-line last:border-0">
                <td className="px-3 py-2 font-mono text-xs">{a.alias}</td>
                <td className="px-3 py-2 text-ink-muted">{a.source}</td>
              </tr>
            ))}
            {aliases.length === 0 && (
              <tr>
                <td colSpan={2} className="px-3 py-6 text-center text-ink-faint">
                  No aliases for this district.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <form onSubmit={add} className="flex flex-wrap items-end gap-3 rounded-[6px] border border-line bg-surface p-4">
        <Input label="Alias" value={alias} onChange={setAlias} required />
        <Input
          label="Source"
          value={source}
          onChange={setSource}
          placeholder="village in Chikhli taluka"
        />
        <button className="inline-flex h-8 items-center rounded-[4px] bg-[var(--brand)] px-3 text-[length:var(--text-sm)] font-medium text-white transition-colors duration-[var(--duration)] hover:bg-[var(--brand-hover)]">Add</button>
      </form>
    </>
  );
}

function Keys({ onError, onNotice }: Handlers) {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [form, setForm] = useState({ name: "", department_id: "" });
  const [issued, setIssued] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    Promise.all([
      apiJson<ApiKey[]>("/api/v1/admin/api-keys"),
      apiJson<Department[]>("/api/v1/departments"),
    ])
      .then(([k, d]) => {
        if (!active) return;
        setKeys(k);
        setDepartments(d);
      })
      .catch((e) => active && onError(e.message));
    return () => {
      active = false;
    };
  }, [reload, onError]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    onError(null);
    try {
      const created = await postJson<{ api_key: string }>("/api/v1/admin/api-keys", {
        name: form.name,
        department_id: form.department_id,
        scopes: ["cameras:read", "cameras:write"],
      });
      setIssued(created.api_key);
      onNotice("Key created. Copy it now — it is never shown again.");
      setForm({ name: "", department_id: "" });
      setReload((n) => n + 1);
    } catch (e) {
      onError(e instanceof ApiError ? e.message : "Could not create the key.");
    }
  }

  async function revoke(id: string) {
    await apiFetch(`/api/v1/admin/api-keys/${id}`, { method: "DELETE" });
    setReload((n) => n + 1);
  }

  return (
    <>
      {issued && (
        <div className="mb-4 rounded border border-amber-300 bg-amber-50 p-3">
          <p className="mb-1 text-xs font-semibold uppercase text-amber-800">
            Copy this now — it is not stored and cannot be shown again
          </p>
          <code className="block break-all text-sm">{issued}</code>
        </div>
      )}

      <div className="mb-6 overflow-x-auto rounded-[6px] border border-line bg-surface">
        <table className="w-full text-sm">
          <thead className="border-b border-line bg-sunken text-left text-[length:var(--text-2xs)] font-semibold uppercase tracking-[0.04em] text-ink-faint">
            <tr>
              <th className="px-3 py-2">Name</th>
              <th className="px-3 py-2">Prefix</th>
              <th className="px-3 py-2">Scopes</th>
              <th className="px-3 py-2">State</th>
              <th className="px-3 py-2" />
            </tr>
          </thead>
          <tbody>
            {keys.map((key) => (
              <tr key={key.id} className="border-b border-line last:border-0">
                <td className="px-3 py-2">{key.name}</td>
                <td className="px-3 py-2 font-mono text-xs">{key.key_prefix}…</td>
                <td className="px-3 py-2 text-[length:var(--text-xs)] text-ink-muted">
                  {key.scopes.join(", ")}
                </td>
                <td className="px-3 py-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs ${
                      key.revoked_at
                        ? "bg-red-100 text-red-800"
                        : "bg-green-100 text-green-800"
                    }`}
                  >
                    {key.revoked_at ? "revoked" : "active"}
                  </span>
                </td>
                <td className="px-3 py-2 text-right">
                  {!key.revoked_at && (
                    <button
                      onClick={() => revoke(key.id)}
                      className="text-xs text-red-600 underline-offset-2 hover:underline"
                    >
                      Revoke
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {keys.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-6 text-center text-ink-faint">
                  No API keys issued.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <form onSubmit={create} className="flex flex-wrap items-end gap-3 rounded-[6px] border border-line bg-surface p-4">
        <Input label="Name" value={form.name} onChange={(v) => setForm({ ...form, name: v })} required />
        <label className="block">
          <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
            Department
          </span>
          <select
            className="rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
            required
            value={form.department_id}
            onChange={(e) => setForm({ ...form, department_id: e.target.value })}
          >
            <option value="">Select…</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.code}
              </option>
            ))}
          </select>
        </label>
        <button className="inline-flex h-8 items-center rounded-[4px] bg-[var(--brand)] px-3 text-[length:var(--text-sm)] font-medium text-white transition-colors duration-[var(--duration)] hover:bg-[var(--brand-hover)]">
          Issue key
        </button>
      </form>
    </>
  );
}

function Audit({ onError }: { onError: (m: string | null) => void }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);

  useEffect(() => {
    let active = true;
    apiJson<AuditEntry[]>("/api/v1/admin/audit-logs?limit=100")
      .then((e) => active && setEntries(e))
      .catch((e) => active && onError(e.message));
    return () => {
      active = false;
    };
  }, [onError]);

  return (
    <div className="overflow-x-auto rounded-[6px] border border-line bg-surface">
      <table className="w-full text-sm">
        <thead className="border-b border-line bg-sunken text-left text-[length:var(--text-2xs)] font-semibold uppercase tracking-[0.04em] text-ink-faint">
          <tr>
            <th className="px-3 py-2">When</th>
            <th className="px-3 py-2">Action</th>
            <th className="px-3 py-2">Entity</th>
            <th className="px-3 py-2">Who</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.id} className="border-b border-line last:border-0">
              <td className="px-3 py-2 text-[length:var(--text-xs)] text-ink-muted">
                {new Date(entry.at).toLocaleString()}
              </td>
              <td className="px-3 py-2 font-mono text-xs">{entry.action}</td>
              <td className="px-3 py-2 text-ink-muted">{entry.entity_type}</td>
              <td className="px-3 py-2 text-xs">{entry.actor_label ?? "system"}</td>
            </tr>
          ))}
          {entries.length === 0 && (
            <tr>
              <td colSpan={4} className="px-3 py-6 text-center text-ink-faint">
                Nothing recorded yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function Input({
  label,
  value,
  onChange,
  required,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
        {label}
      </span>
      <input
        className="rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
        value={value}
        required={required}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
