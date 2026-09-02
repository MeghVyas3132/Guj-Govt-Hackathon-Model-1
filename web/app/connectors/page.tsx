"use client";

import { useEffect, useState } from "react";

import { ApiError, apiJson, postJson } from "@/lib/session";

type Department = { id: string; code: string; name: string };
type Connector = {
  id: string;
  code: string;
  name: string;
  department_id: string;
  config: Record<string, unknown>;
  is_active: boolean;
};
type Report = { total: number; created: number; skipped: number; failed: number };

const TEMPLATE = `{
  "catalogue_url": "https://vendor.example/api/cameras",
  "auth": {
    "type": "header",
    "name": "X-API-Key",
    "credential_ref": "vendor_key"
  },
  "root_path": null,
  "id_keys": ["id"],
  "endpoint_rules": [
    {
      "protocol": "rtsp",
      "url_key": "rtsp_url",
      "url_template": "rtsp://vendor.example:554/{id}",
      "reachability": "direct_ip",
      "is_primary": true
    }
  ]
}`;

export default function ConnectorsPage() {
  const [departments, setDepartments] = useState<Department[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  const [form, setForm] = useState({
    code: "",
    name: "",
    department_id: "",
    config: TEMPLATE,
  });
  const [credential, setCredential] = useState({ name: "", value: "" });

  useEffect(() => {
    let active = true;
    Promise.all([
      apiJson<Department[]>("/api/v1/departments"),
      apiJson<Connector[]>("/api/v1/connectors"),
    ])
      .then(([depts, conns]) => {
        if (!active) return;
        setDepartments(depts);
        setConnectors(conns);
      })
      .catch((e) => active && setError(e.message));
    return () => {
      active = false;
    };
  }, [reload]);

  async function createConnector(event: React.FormEvent) {
    event.preventDefault();
    setBusy("create");
    setError(null);
    setNotice(null);
    try {
      const config = JSON.parse(form.config);
      await postJson("/api/v1/connectors", {
        code: form.code,
        name: form.name,
        department_id: form.department_id,
        config,
      });
      setNotice(`Connector ${form.code} created. Add its credential, then sync.`);
      setForm({ code: "", name: "", department_id: "", config: TEMPLATE });
      setReload((n) => n + 1);
    } catch (e) {
      if (e instanceof SyntaxError) setError("The config is not valid JSON.");
      else if (e instanceof ApiError) setError(e.message);
      else setError("Could not create the connector.");
    } finally {
      setBusy(null);
    }
  }

  async function saveCredential(event: React.FormEvent) {
    event.preventDefault();
    setBusy("credential");
    setError(null);
    try {
      await postJson("/api/v1/connectors/credentials", credential);
      setNotice(`Credential ${credential.name} stored.`);
      setCredential({ name: "", value: "" });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not store the credential.");
    } finally {
      setBusy(null);
    }
  }

  async function sync(code: string) {
    setBusy(code);
    setError(null);
    setNotice(null);
    try {
      const report = await postJson<Report>(`/api/v1/connectors/${code}/sync`, {});
      setNotice(
        `${code}: ${report.created} created, ${report.skipped} unchanged, ` +
          `${report.failed} failed of ${report.total}.`,
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Sync failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-[56rem] p-6">
      <h1 className="mb-1 text-[length:var(--text-xl)] font-semibold text-ink">Source connectors</h1>
      <p className="mb-6 text-[length:var(--text-sm)] text-ink-muted">
        Onboarding a department&rsquo;s camera system is a row here plus a field mapping.
        No vendor name exists anywhere in the codebase — the config below describes
        the catalogue URL, how to authenticate, where the camera list sits in the
        response, and how to reach each stream.
      </p>

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

      <section className="mb-8">
        <h2 className="mb-3 text-[length:var(--text-lg)] font-semibold text-ink">
          Configured sources
        </h2>
        {connectors.length === 0 ? (
          <p className="rounded-[6px] border border-line bg-surface p-4 text-sm text-ink-faint">
            No connectors yet.
          </p>
        ) : (
          <ul className="space-y-2">
            {connectors.map((connector) => {
              const auth = (connector.config.auth ?? {}) as Record<string, string>;
              return (
                <li
                  key={connector.id}
                  className="flex flex-wrap items-center gap-3 rounded-[6px] border border-line bg-surface p-3"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">
                      <span className="font-mono">{connector.code}</span> — {connector.name}
                    </p>
                    <p className="truncate text-[length:var(--text-xs)] text-ink-muted">
                      {String(connector.config.catalogue_url)}
                    </p>
                    <p className="text-[length:var(--text-xs)] text-ink-faint">
                      auth: {auth.type ?? "none"}
                      {auth.name ? ` (${auth.name})` : ""} ·{" "}
                      {
                        ((connector.config.endpoint_rules ?? []) as unknown[]).length
                      }{" "}
                      stream rules
                    </p>
                  </div>
                  <button
                    onClick={() => sync(connector.code)}
                    disabled={busy !== null}
                    className="inline-flex h-8 items-center rounded-[4px] bg-[var(--brand)] px-3 text-[length:var(--text-sm)] font-medium text-white transition-colors duration-[var(--duration)] hover:bg-[var(--brand-hover)] disabled:opacity-40"
                  >
                    {busy === connector.code ? "Syncing…" : "Sync now"}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="mb-8 rounded-[6px] border border-line bg-surface p-4">
        <h2 className="mb-3 text-[length:var(--text-lg)] font-semibold text-ink">
          Store a credential
        </h2>
        <p className="mb-3 text-[length:var(--text-xs)] text-ink-muted">
          Connector config references secrets by name and never contains one. The value
          is write-only — the API never returns it.
        </p>
        <form onSubmit={saveCredential} className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
              Reference name
            </span>
            <input
              className="rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
              placeholder="vendor_key"
              required
              value={credential.name}
              onChange={(e) => setCredential({ ...credential, name: e.target.value })}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
              Secret
            </span>
            <input
              className="rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
              type="password"
              required
              value={credential.value}
              onChange={(e) => setCredential({ ...credential, value: e.target.value })}
            />
          </label>
          <button
            type="submit"
            disabled={busy !== null}
            className="inline-flex h-8 items-center rounded-[4px] border border-line-strong bg-surface px-3 text-[length:var(--text-sm)] font-medium transition-colors duration-[var(--duration)] hover:bg-sunken disabled:opacity-40"
          >
            Store
          </button>
        </form>
      </section>

      <section className="rounded-[6px] border border-line bg-surface p-4">
        <h2 className="mb-3 text-[length:var(--text-lg)] font-semibold text-ink">
          Add a source
        </h2>
        <form onSubmit={createConnector} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-3">
            <label className="block">
              <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
                Code
              </span>
              <input
                className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
                placeholder="rto"
                required
                value={form.code}
                onChange={(e) => setForm({ ...form, code: e.target.value })}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
                Name
              </span>
              <input
                className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
                Department
              </span>
              <select
                className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
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
          </div>

          <label className="block">
            <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
              Config
            </span>
            <textarea
              className="h-72 w-full rounded-[6px] border border-line bg-surface p-3 font-mono text-xs"
              spellCheck={false}
              value={form.config}
              onChange={(e) => setForm({ ...form, config: e.target.value })}
            />
            <span className="mt-1 block text-[length:var(--text-xs)] text-ink-faint">
              Validated on save, so a bad rule is caught now rather than mid-sync.
            </span>
          </label>

          <button
            type="submit"
            disabled={busy !== null}
            className="inline-flex h-8 items-center rounded-[4px] bg-[var(--brand)] px-3 text-[length:var(--text-sm)] font-medium text-white transition-colors duration-[var(--duration)] hover:bg-[var(--brand-hover)] disabled:opacity-40"
          >
            {busy === "create" ? "Creating…" : "Create connector"}
          </button>
        </form>
      </section>
    </div>
  );
}
