"use client";

/**
 * Event subscriptions. The page an integrator is sent to when they ask "how do
 * we know when a camera goes down".
 *
 * Delivery history is on the same screen as the subscription rather than behind
 * a drilldown, because the only reason anyone opens this page twice is to find
 * out why an alert did not arrive.
 */

import { useCallback, useEffect, useState } from "react";

import {
  Button,
  EmptyState,
  Field,
  Input,
  Mono,
  Notice,
  Panel,
  PageHeader,
  SectionTitle,
  Select,
  StatusBadge,
  TD,
  TH,
  THead,
  TR,
  Table,
  Tag,
} from "@/components/ui";
import { apiFetch } from "@/lib/session";

type Webhook = {
  id: string;
  name: string;
  url: string;
  events: string[];
  department_id: string | null;
  secret_ref: string | null;
  is_active: boolean;
  consecutive_failures: number;
  disabled_at: string | null;
  last_delivered_at: string | null;
};

type Delivery = {
  id: string;
  event: string;
  status_code: number | null;
  succeeded: boolean;
  duration_ms: number | null;
  error: string | null;
  created_at: string | null;
};

export default function WebhooksPage() {
  const [hooks, setHooks] = useState<Webhook[]>([]);
  const [events, setEvents] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [hookRes, eventRes] = await Promise.all([
        apiFetch("/api/v1/webhooks"),
        apiFetch("/api/v1/webhooks/events"),
      ]);
      if (!hookRes.ok) throw new Error(`Could not list subscriptions (${hookRes.status})`);
      setHooks(await hookRes.json());
      if (eventRes.ok) setEvents(await eventRes.json());
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Failed to load");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="mx-auto max-w-[76rem] p-6">
      <PageHeader
        title="Event subscriptions"
        description="Where the registry pushes alerts when a camera changes state. Payloads are signed, so a receiver can tell a genuine alert from anything else that reaches its URL."
      />

      {error && (
        <div className="mb-4">
          <Notice tone="error" title="Could not load subscriptions">
            {error}
          </Notice>
        </div>
      )}
      {notice && (
        <div className="mb-4">
          <Notice tone="success">{notice}</Notice>
        </div>
      )}

      <div className="mb-8">
        <SectionTitle>New subscription</SectionTitle>
        <CreateForm
          events={events}
          onCreated={async (name) => {
            setNotice(`Subscribed “${name}”.`);
            await load();
          }}
          onError={setError}
        />
      </div>

      <SectionTitle>Subscriptions</SectionTitle>
      {hooks.length === 0 ? (
        <Panel>
          <EmptyState title="Nothing is subscribed yet">
            Add a URL above and the registry will POST a signed JSON payload to it
            whenever a camera changes state. Verify the signature before trusting a
            delivery.
          </EmptyState>
        </Panel>
      ) : (
        <div className="flex flex-col gap-3">
          {hooks.map((hook) => (
            <HookCard
              key={hook.id}
              hook={hook}
              expanded={expanded === hook.id}
              onToggle={() => setExpanded(expanded === hook.id ? null : hook.id)}
              onChanged={load}
              onNotice={setNotice}
            />
          ))}
        </div>
      )}

      <div className="mt-8">
        <SectionTitle>Verifying a delivery</SectionTitle>
        <Panel>
          <p className="mb-3 text-[length:var(--text-sm)] text-ink-muted">
            Each request carries <Mono>X-Sentinel-Timestamp</Mono> and{" "}
            <Mono>X-Sentinel-Signature</Mono>. Recompute the HMAC over the timestamp,
            a literal dot, and the raw request body — the timestamp is inside the
            signed material so a captured payload cannot be replayed later.
          </p>
          <pre className="overflow-x-auto rounded-[4px] bg-sunken p-3 font-mono text-[length:var(--text-xs)] leading-relaxed text-ink">
{`expected = "sha256=" + hmac_sha256(
    key = <your secret>,
    msg = timestamp + "." + raw_body,
).hexdigest()

# Compare with hmac.compare_digest, not ==, and reject
# a timestamp older than a few minutes.`}
          </pre>
        </Panel>
      </div>
    </div>
  );
}

function CreateForm({
  events,
  onCreated,
  onError,
}: {
  events: string[];
  onCreated: (name: string) => void;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [secretRef, setSecretRef] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  return (
    <Panel>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name" required hint="How this appears in the delivery log">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Control room dashboard"
          />
        </Field>
        <Field label="URL" required hint="Must be http or https">
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://ops.example.gov.in/hooks/sentinel"
          />
        </Field>
        <Field
          label="Signing secret"
          hint="Names a row in credentials — never the secret itself. Leave blank to send unsigned."
        >
          <Input
            value={secretRef}
            onChange={(e) => setSecretRef(e.target.value)}
            placeholder="ops_hook_secret"
          />
        </Field>
        <Field
          label="Events"
          hint="Select none to receive every event, including ones added later."
        >
          <div className="flex flex-wrap gap-1.5 pt-1">
            {events.map((event) => {
              const on = selected.includes(event);
              return (
                <button
                  key={event}
                  type="button"
                  aria-pressed={on}
                  onClick={() =>
                    setSelected(
                      on ? selected.filter((e) => e !== event) : [...selected, event],
                    )
                  }
                  className={`rounded-[4px] border px-2 py-1 font-mono text-[length:var(--text-2xs)] transition-colors duration-[var(--duration)] ${
                    on
                      ? "border-transparent bg-[var(--brand-tint)] text-[var(--brand)]"
                      : "border-line-strong bg-surface text-ink-muted hover:bg-sunken"
                  }`}
                >
                  {event}
                </button>
              );
            })}
          </div>
        </Field>
      </div>

      <div className="mt-4">
        <Button
          variant="primary"
          busy={busy}
          disabled={!name.trim() || !url.trim()}
          onClick={async () => {
            setBusy(true);
            try {
              const response = await apiFetch("/api/v1/webhooks", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  name: name.trim(),
                  url: url.trim(),
                  events: selected,
                  secret_ref: secretRef.trim() || null,
                }),
              });
              if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                throw new Error(
                  typeof body.detail === "string"
                    ? body.detail
                    : `Could not subscribe (${response.status})`,
                );
              }
              onCreated(name.trim());
              setName("");
              setUrl("");
              setSecretRef("");
              setSelected([]);
            } catch (cause) {
              onError(cause instanceof Error ? cause.message : "Failed");
            } finally {
              setBusy(false);
            }
          }}
        >
          Subscribe
        </Button>
      </div>
    </Panel>
  );
}

function HookCard({
  hook,
  expanded,
  onToggle,
  onChanged,
  onNotice,
}: {
  hook: Webhook;
  expanded: boolean;
  onToggle: () => void;
  onChanged: () => Promise<void>;
  onNotice: (message: string) => void;
}) {
  const [deliveries, setDeliveries] = useState<Delivery[] | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    apiFetch(`/api/v1/webhooks/${hook.id}/deliveries?limit=20`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setDeliveries)
      .catch(() => setDeliveries([]));
  }, [expanded, hook.id]);

  const health = hook.disabled_at
    ? "offline"
    : hook.consecutive_failures > 0
      ? "maintenance"
      : "online";

  return (
    <Panel padded={false}>
      <div className="flex flex-wrap items-start justify-between gap-3 p-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-ink">{hook.name}</span>
            <StatusBadge status={health} />
            {!hook.is_active && <Tag>paused</Tag>}
            {hook.secret_ref ? <Tag>signed</Tag> : <Tag>unsigned</Tag>}
          </div>
          <p className="mt-1 break-all">
            <Mono className="text-ink-muted">{hook.url}</Mono>
          </p>
          <p className="mt-1.5 flex flex-wrap gap-1">
            {hook.events.length === 0 ? (
              <span className="text-[length:var(--text-xs)] text-ink-faint">
                all events
              </span>
            ) : (
              hook.events.map((e) => <Tag key={e}>{e}</Tag>)
            )}
          </p>
          {hook.disabled_at && (
            <p className="mt-2 text-[length:var(--text-xs)] text-[var(--state-offline-ink)]">
              Disabled automatically after {hook.consecutive_failures} consecutive
              failures. Re-enable to resume.
            </p>
          )}
        </div>

        <div className="flex shrink-0 flex-wrap gap-2">
          <Button
            busy={testing}
            onClick={async () => {
              setTesting(true);
              try {
                const response = await apiFetch(`/api/v1/webhooks/${hook.id}/test`, {
                  method: "POST",
                });
                const body = await response.json();
                onNotice(
                  body.succeeded
                    ? `Test delivered — HTTP ${body.status_code} in ${body.duration_ms}ms${body.signed ? ", signed" : ", unsigned"}.`
                    : `Test failed — ${body.error ?? `HTTP ${body.status_code}`}.`,
                );
                await onChanged();
              } finally {
                setTesting(false);
              }
            }}
          >
            Send test
          </Button>
          <Button
            onClick={async () => {
              await apiFetch(`/api/v1/webhooks/${hook.id}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ is_active: !hook.is_active }),
              });
              await onChanged();
            }}
          >
            {hook.is_active ? "Pause" : "Resume"}
          </Button>
          <Button variant="subtle" onClick={onToggle}>
            {expanded ? "Hide log" : "Delivery log"}
          </Button>
          <Button
            variant="danger"
            onClick={async () => {
              await apiFetch(`/api/v1/webhooks/${hook.id}`, { method: "DELETE" });
              await onChanged();
            }}
          >
            Delete
          </Button>
        </div>
      </div>

      {expanded && (
        <div className="border-t border-line">
          {deliveries === null ? (
            <p className="p-4 text-[length:var(--text-sm)] text-ink-faint">Loading…</p>
          ) : deliveries.length === 0 ? (
            <EmptyState title="No deliveries yet">
              Nothing has been sent to this URL. Use “Send test” to check the
              endpoint before a real outage does it for you.
            </EmptyState>
          ) : (
            <Table>
              <THead>
                <TR>
                  <TH>When</TH>
                  <TH>Event</TH>
                  <TH align="right">Status</TH>
                  <TH align="right">Took</TH>
                  <TH>Error</TH>
                </TR>
              </THead>
              <tbody>
                {deliveries.map((d) => (
                  <TR key={d.id}>
                    <TD>
                      <Mono className="text-ink-muted">
                        {d.created_at?.slice(0, 19).replace("T", " ") ?? "—"}
                      </Mono>
                    </TD>
                    <TD>
                      <Mono>{d.event}</Mono>
                    </TD>
                    <TD align="right">
                      <span
                        className="font-mono text-[length:var(--text-xs)]"
                        style={{
                          color: d.succeeded
                            ? "var(--state-online-ink)"
                            : "var(--state-offline-ink)",
                        }}
                      >
                        {d.status_code ?? "—"}
                      </span>
                    </TD>
                    <TD align="right">
                      <span className="tabular-nums text-ink-muted">
                        {d.duration_ms ? `${d.duration_ms}ms` : "—"}
                      </span>
                    </TD>
                    <TD>
                      <span className="text-[length:var(--text-xs)] text-ink-muted">
                        {d.error ? d.error.slice(0, 120) : "—"}
                      </span>
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </div>
      )}
    </Panel>
  );
}
