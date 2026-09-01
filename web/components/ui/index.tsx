"use client";

/**
 * The interface vocabulary.
 *
 * One button height, one radius, one focus treatment, one status badge. A "save"
 * button that looks different on two screens means one of them is wrong, so these
 * are the only versions.
 */

import type { ReactNode } from "react";

/* ── Panel ─────────────────────────────────────────────────────────────── */

export function Panel({
  children,
  className = "",
  padded = true,
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
}) {
  return (
    <div
      className={`rounded-[6px] border border-line bg-surface ${padded ? "p-4" : ""} ${className}`}
    >
      {children}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="max-w-[68ch]">
        <h1 className="text-[length:var(--text-xl)] font-semibold text-ink">{title}</h1>
        {description && (
          <p className="mt-1 text-[length:var(--text-sm)] text-ink-muted">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex shrink-0 gap-2">{actions}</div>}
    </header>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <h2 className="mb-2 text-[length:var(--text-lg)] font-semibold text-ink">
      {children}
    </h2>
  );
}

/* ── Button ────────────────────────────────────────────────────────────── */

type ButtonVariant = "primary" | "default" | "subtle" | "danger";

const BUTTON: Record<ButtonVariant, string> = {
  primary:
    "bg-[var(--brand)] text-white hover:bg-[var(--brand-hover)] border border-transparent",
  default:
    "bg-surface text-ink border border-line-strong hover:bg-sunken",
  subtle: "bg-transparent text-ink-muted border border-transparent hover:bg-sunken hover:text-ink",
  danger:
    "bg-surface text-[var(--state-offline-ink)] border border-line-strong hover:bg-[var(--state-offline-bg)]",
};

export function Button({
  variant = "default",
  busy,
  className = "",
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  busy?: boolean;
}) {
  return (
    <button
      {...props}
      disabled={props.disabled || busy}
      aria-busy={busy || undefined}
      className={`inline-flex h-8 items-center gap-1.5 rounded-[4px] px-3 text-[length:var(--text-sm)] font-medium
        transition-[background-color,border-color,color] duration-[var(--duration)] ease-[var(--ease)]
        disabled:cursor-not-allowed disabled:opacity-45
        ${BUTTON[variant]} ${className}`}
    >
      {busy && <Spinner />}
      {children}
    </button>
  );
}

export function LinkButton({
  variant = "default",
  className = "",
  children,
  ...props
}: React.AnchorHTMLAttributes<HTMLAnchorElement> & { variant?: ButtonVariant }) {
  return (
    <a
      {...props}
      className={`inline-flex h-8 items-center gap-1.5 rounded-[4px] px-3 text-[length:var(--text-sm)] font-medium
        transition-[background-color,border-color,color] duration-[var(--duration)] ease-[var(--ease)]
        ${BUTTON[variant]} ${className}`}
    >
      {children}
    </a>
  );
}

function Spinner() {
  return (
    <span
      aria-hidden
      className="size-3 animate-spin rounded-full border-[1.5px] border-current border-r-transparent opacity-70"
    />
  );
}

/* ── Status ────────────────────────────────────────────────────────────── */

const STATE_VAR: Record<string, string> = {
  online: "online",
  offline: "offline",
  maintenance: "maintenance",
};

/**
 * Status is never colour alone: the word is always present, so the meaning
 * survives greyscale printing, projection, and colour-blind readers.
 */
export function StatusBadge({ status }: { status: string }) {
  const key = STATE_VAR[status] ?? "unknown";
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-[length:var(--text-2xs)] font-medium"
      style={{
        color: `var(--state-${key}-ink)`,
        background: `var(--state-${key}-bg)`,
      }}
    >
      <span
        aria-hidden
        className="size-1.5 rounded-full"
        style={{ background: "currentColor" }}
      />
      {status}
    </span>
  );
}

export function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-[4px] bg-sunken px-1.5 py-0.5 text-[length:var(--text-2xs)] font-medium text-ink-muted">
      {children}
    </span>
  );
}

export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span className={`whitespace-nowrap font-mono text-[length:var(--text-xs)] ${className}`}>
      {children}
    </span>
  );
}

/* ── Table ─────────────────────────────────────────────────────────────── */

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-[6px] border border-line bg-surface">
      <table className="w-full border-collapse text-[length:var(--text-sm)]">
        {children}
      </table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return (
    <thead className="sticky top-0 z-[var(--z-sticky)] bg-sunken text-left">
      {children}
    </thead>
  );
}

export function TH({
  children,
  align = "left",
}: {
  children?: ReactNode;
  align?: "left" | "right";
}) {
  return (
    <th
      scope="col"
      className={`border-b border-line px-3 py-2 text-[length:var(--text-2xs)] font-semibold uppercase tracking-[0.04em] text-ink-faint ${
        align === "right" ? "text-right" : ""
      }`}
    >
      {children}
    </th>
  );
}

export function TR({ children }: { children: ReactNode }) {
  return (
    <tr className="border-b border-line transition-colors duration-[var(--duration)] last:border-0 hover:bg-sunken/60">
      {children}
    </tr>
  );
}

export function TD({
  children,
  align = "left",
  className = "",
}: {
  children?: ReactNode;
  align?: "left" | "right";
  className?: string;
}) {
  return (
    <td
      className={`px-3 py-2 align-top ${align === "right" ? "text-right" : ""} ${className}`}
    >
      {children}
    </td>
  );
}

/* ── Form ──────────────────────────────────────────────────────────────── */

export function Field({
  label,
  hint,
  error,
  required,
  children,
}: {
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[length:var(--text-xs)] font-medium text-ink-muted">
        {label}
        {required && (
          <span className="ml-1 text-[var(--state-offline-ink)]" aria-hidden>
            *
          </span>
        )}
      </span>
      {children}
      {error ? (
        <span className="mt-1 block text-[length:var(--text-xs)] text-[var(--state-offline-ink)]">
          {error}
        </span>
      ) : (
        hint && (
          <span className="mt-1 block text-[length:var(--text-xs)] text-ink-faint">
            {hint}
          </span>
        )
      )}
    </label>
  );
}

const CONTROL =
  "w-full rounded-[4px] border border-line-strong bg-surface px-2.5 h-8 text-[length:var(--text-sm)] text-ink " +
  "transition-[border-color] duration-[var(--duration)] hover:border-[var(--ink-faint)] " +
  "disabled:cursor-not-allowed disabled:bg-sunken disabled:text-ink-faint";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${CONTROL} ${props.className ?? ""}`} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${CONTROL} ${props.className ?? ""}`} />;
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={`w-full rounded-[4px] border border-line-strong bg-surface p-2.5 font-mono text-[length:var(--text-xs)] leading-relaxed text-ink
        transition-[border-color] duration-[var(--duration)] hover:border-[var(--ink-faint)] ${props.className ?? ""}`}
    />
  );
}

/* ── Feedback ──────────────────────────────────────────────────────────── */

type NoticeTone = "info" | "warn" | "error" | "success";

const NOTICE: Record<NoticeTone, { border: string; bg: string; ink: string }> = {
  info: { border: "var(--border-strong)", bg: "var(--surface-sunken)", ink: "var(--ink)" },
  warn: {
    border: "color-mix(in oklch, var(--state-maintenance-ink) 30%, transparent)",
    bg: "var(--state-maintenance-bg)",
    ink: "var(--state-maintenance-ink)",
  },
  error: {
    border: "color-mix(in oklch, var(--state-offline-ink) 30%, transparent)",
    bg: "var(--state-offline-bg)",
    ink: "var(--state-offline-ink)",
  },
  success: {
    border: "color-mix(in oklch, var(--state-online-ink) 30%, transparent)",
    bg: "var(--state-online-bg)",
    ink: "var(--state-online-ink)",
  },
};

export function Notice({
  tone = "info",
  title,
  children,
}: {
  tone?: NoticeTone;
  title?: string;
  children: ReactNode;
}) {
  const style = NOTICE[tone];
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className="rounded-[6px] border p-3 text-[length:var(--text-sm)]"
      style={{ borderColor: style.border, background: style.bg, color: style.ink }}
    >
      {title && <p className="mb-1 font-semibold">{title}</p>}
      <div className="[&_strong]:font-semibold">{children}</div>
    </div>
  );
}

/**
 * Empty states name what would be here and what to do next. "No data" tells a
 * user nothing they did not already know.
 */
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <p className="text-[length:var(--text-sm)] font-medium text-ink">{title}</p>
      {children && (
        <p className="max-w-[46ch] text-[length:var(--text-xs)] text-ink-muted">
          {children}
        </p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** Skeletons keep the layout still while data arrives; a centred spinner does not. */
export function SkeletonRows({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className="border-b border-line last:border-0">
          {Array.from({ length: cols }).map((__, c) => (
            <td key={c} className="px-3 py-2.5">
              <span
                className="block h-3 animate-pulse rounded-[3px] bg-sunken"
                style={{ width: `${55 + ((r * 7 + c * 13) % 40)}%` }}
              />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

export function Metric({
  label,
  value,
  foot,
  tone,
}: {
  label: string;
  value: ReactNode;
  foot?: ReactNode;
  tone?: "default" | "warn";
}) {
  return (
    <div
      className="rounded-[6px] border p-4"
      style={
        tone === "warn"
          ? {
              borderColor:
                "color-mix(in oklch, var(--state-maintenance-ink) 30%, transparent)",
              background: "var(--state-maintenance-bg)",
            }
          : { borderColor: "var(--border)", background: "var(--surface)" }
      }
    >
      <p
        className="text-[length:var(--text-2xs)] font-semibold uppercase tracking-[0.04em]"
        style={{
          color: tone === "warn" ? "var(--state-maintenance-ink)" : "var(--ink-faint)",
        }}
      >
        {label}
      </p>
      <p
        className="metric mt-1 text-[length:var(--text-2xl)] font-semibold leading-none"
        style={{
          color: tone === "warn" ? "var(--state-maintenance-ink)" : "var(--ink)",
        }}
      >
        {value}
      </p>
      {foot && (
        <p
          className="mt-1.5 text-[length:var(--text-xs)]"
          style={{
            color: tone === "warn" ? "var(--state-maintenance-ink)" : "var(--ink-muted)",
          }}
        >
          {foot}
        </p>
      )}
    </div>
  );
}

export function Toolbar({ children }: { children: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-end gap-3 rounded-[6px] border border-line bg-sunken p-3">
      {children}
    </div>
  );
}
