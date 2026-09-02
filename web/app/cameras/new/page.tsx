"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { ApiError, apiJson, postJson, useCurrentUser } from "@/lib/session";

type Department = { id: string; code: string; name: string };
type Term = { code: string; label: string };
type RowError = { code: string; message: string; field: string | null };

export default function NewCameraPage() {
  const router = useRouter();
  const user = useCurrentUser();

  const [departments, setDepartments] = useState<Department[]>([]);
  const [cameraTypes, setCameraTypes] = useState<Term[]>([]);
  const [siteTypes, setSiteTypes] = useState<Term[]>([]);
  const [errors, setErrors] = useState<RowError[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [form, setForm] = useState({
    department_id: "",
    external_camera_id: "",
    name: "",
    latitude: "",
    longitude: "",
    address: "",
    camera_type: "fixed",
    site_type: "other",
    azimuth_deg: "",
    fov_deg: "",
    range_m: "",
  });

  useEffect(() => {
    let active = true;
    Promise.all([
      apiJson<Department[]>("/api/v1/departments"),
      apiJson<Term[]>("/api/v1/vocabulary/camera_type"),
      apiJson<Term[]>("/api/v1/vocabulary/site_type"),
    ])
      .then(([depts, types, sites]) => {
        if (!active) return;
        setDepartments(depts);
        setCameraTypes(types);
        setSiteTypes(sites);
      })
      .catch(() => active && setMessage("Could not load form options."));
    return () => {
      active = false;
    };
  }, []);

  // A department admin can only write to their own department, so preselect it
  // rather than letting them pick one the API will reject.
  const ownDepartment = user?.department_id ?? null;
  const selectable =
    user?.role === "super_admin"
      ? departments
      : departments.filter((d) => d.id === ownDepartment);

  function update(field: string, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setErrors([]);
    setMessage(null);

    const numeric = (value: string) => (value === "" ? undefined : Number(value));

    try {
      const created = await postJson<{ id: string; camera_uid: string }>(
        "/api/v1/cameras",
        {
          department_id: form.department_id || selectable[0]?.id,
          external_camera_id: form.external_camera_id,
          name: form.name || undefined,
          latitude: Number(form.latitude),
          longitude: Number(form.longitude),
          address: form.address || undefined,
          camera_type: form.camera_type,
          site_type: form.site_type,
          azimuth_deg: numeric(form.azimuth_deg),
          fov_deg: numeric(form.fov_deg),
          range_m: numeric(form.range_m),
        },
      );
      router.push(`/cameras/${created.id}`);
    } catch (e) {
      if (e instanceof ApiError) {
        const detail = (e.body as { detail?: { errors?: RowError[] } })?.detail;
        // The API returns per-field reasons; showing them beats "invalid input".
        if (detail?.errors?.length) setErrors(detail.errors);
        else setMessage(e.message);
      } else {
        setMessage("Could not reach the registry.");
      }
    } finally {
      setBusy(false);
    }
  }

  const directional = !["ptz", "dome"].includes(form.camera_type);

  return (
    <main className="mx-auto max-w-[44rem] p-6">
      <h1 className="mb-1 text-[length:var(--text-xl)] font-semibold text-ink">Add a camera</h1>
      <p className="mb-6 text-[length:var(--text-sm)] text-ink-muted">
        Goes through the same validation, vocabulary and dedupe as a CSV import or a
        vendor sync. Re-entering an external id updates that camera rather than
        creating a second one.
      </p>

      {message && (
        <p className="mb-4 rounded-[6px] border p-3 text-[length:var(--text-sm)] [border-color:color-mix(in_oklch,var(--state-offline-ink)_30%,transparent)] [background:var(--state-offline-bg)] [color:var(--state-offline-ink)]">
          {message}
        </p>
      )}

      {errors.length > 0 && (
        <div className="mb-4 rounded border [border-color:color-mix(in_oklch,var(--state-offline-ink)_30%,transparent)] bg-[var(--state-offline-bg)] p-3 text-sm">
          <p className="mb-2 font-medium text-[var(--state-offline-ink)]">This camera was not accepted:</p>
          <ul className="space-y-1">
            {errors.map((error) => (
              <li key={`${error.field}-${error.code}`} className="text-[var(--state-offline-ink)]">
                <span className="font-mono text-xs">{error.field ?? "—"}</span>{" "}
                {error.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <form onSubmit={submit} className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Department" required>
            <select
              className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
              required
              value={form.department_id}
              onChange={(e) => update("department_id", e.target.value)}
            >
              <option value="">Select…</option>
              {selectable.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.code} — {d.name}
                </option>
              ))}
            </select>
          </Field>

          <Field label="External camera id" required hint="The department's own id">
            <input
              className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
              required
              value={form.external_camera_id}
              onChange={(e) => update("external_camera_id", e.target.value)}
            />
          </Field>
        </div>

        <Field label="Name">
          <input
            className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
            placeholder="Nehru Bridge East Approach"
            value={form.name}
            onChange={(e) => update("name", e.target.value)}
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Latitude" required hint="Decimal degrees, inside Gujarat">
            <input
              className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
              required
              inputMode="decimal"
              placeholder="23.0225"
              value={form.latitude}
              onChange={(e) => update("latitude", e.target.value)}
            />
          </Field>
          <Field label="Longitude" required>
            <input
              className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
              required
              inputMode="decimal"
              placeholder="72.5714"
              value={form.longitude}
              onChange={(e) => update("longitude", e.target.value)}
            />
          </Field>
        </div>

        <Field label="Address">
          <input
            className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
            value={form.address}
            onChange={(e) => update("address", e.target.value)}
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Camera type">
            <select
              className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
              value={form.camera_type}
              onChange={(e) => update("camera_type", e.target.value)}
            >
              {cameraTypes.map((t) => (
                <option key={t.code} value={t.code}>
                  {t.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Site type">
            <select
              className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
              value={form.site_type}
              onChange={(e) => update("site_type", e.target.value)}
            >
              {siteTypes.map((t) => (
                <option key={t.code} value={t.code}>
                  {t.label}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <fieldset className="rounded-[6px] border border-line bg-surface p-4">
          <legend className="px-1 text-[length:var(--text-xs)] font-medium text-ink-muted">
            Optics
          </legend>
          <p className="mb-3 text-[length:var(--text-xs)] text-ink-muted">
            {directional
              ? "A recorded bearing makes this camera's coverage a directional wedge in the gap analysis. Leaving it blank treats the camera as seeing in every direction, which overstates its contribution."
              : "Sweeping cameras are modelled as omnidirectional, so a bearing is not used."}
          </p>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Azimuth °" hint="0 = north">
              <input
                className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)] disabled:bg-sunken"
                inputMode="decimal"
                disabled={!directional}
                placeholder="135"
                value={form.azimuth_deg}
                onChange={(e) => update("azimuth_deg", e.target.value)}
              />
            </Field>
            <Field label="Field of view °">
              <input
                className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)] disabled:bg-sunken"
                inputMode="decimal"
                disabled={!directional}
                placeholder="90"
                value={form.fov_deg}
                onChange={(e) => update("fov_deg", e.target.value)}
              />
            </Field>
            <Field label="Range m">
              <input
                className="w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)]"
                inputMode="decimal"
                placeholder="100"
                value={form.range_m}
                onChange={(e) => update("range_m", e.target.value)}
              />
            </Field>
          </div>
        </fieldset>

        <div className="flex gap-3 pt-2">
          <button
            type="submit"
            disabled={busy}
            className="inline-flex h-8 items-center rounded-[4px] bg-[var(--brand)] px-3 text-[length:var(--text-sm)] font-medium text-white transition-colors duration-[var(--duration)] hover:bg-[var(--brand-hover)] disabled:opacity-40"
          >
            {busy ? "Saving…" : "Add camera"}
          </button>
          <button
            type="button"
            onClick={() => router.push("/cameras")}
            className="inline-flex h-8 items-center rounded-[4px] border border-line-strong bg-surface px-3 text-[length:var(--text-sm)] transition-colors duration-[var(--duration)] hover:bg-sunken"
          >
            Cancel
          </button>
        </div>
      </form>
    </main>
  );
}

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
        {label}
        {required && <span className="ml-1 text-[var(--state-offline-ink)]">*</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-[length:var(--text-xs)] text-ink-faint">{hint}</span>}
    </label>
  );
}
