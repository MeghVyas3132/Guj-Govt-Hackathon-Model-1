"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { clearSession, useCurrentUser } from "@/lib/session";

// Filtered by scope, so a viewer is never shown a page that would 403. That is a
// courtesy: the API enforces the same rules independently and a typed URL fails.
const LINKS: { href: string; label: string; scope?: string }[] = [
  { href: "/map", label: "Map", scope: "cameras:read" },
  { href: "/cameras", label: "Cameras", scope: "cameras:read" },
  { href: "/health", label: "Health", scope: "cameras:read" },
  { href: "/coverage", label: "Coverage", scope: "coverage:run" },
  { href: "/onboarding", label: "Onboarding", scope: "cameras:write" },
  { href: "/connectors", label: "Connectors", scope: "admin" },
  { href: "/admin", label: "Admin", scope: "admin" },
];

export function SiteNav() {
  const pathname = usePathname();
  const router = useRouter();
  const user = useCurrentUser();

  if (pathname?.startsWith("/login")) return null;

  const visible = LINKS.filter((l) => !l.scope || user?.scopes.includes(l.scope));

  return (
    <nav className="flex shrink-0 flex-wrap items-center gap-x-1 gap-y-2 border-b border-line bg-surface px-4 py-2">
      <Link
        href="/map"
        className="mr-4 flex items-baseline gap-2 text-[length:var(--text-sm)] font-semibold text-ink"
      >
        Sentinel
        <span className="text-[length:var(--text-2xs)] font-normal text-ink-faint">
          CCTV Registry
        </span>
      </Link>

      {visible.map((link) => {
        const active =
          pathname === link.href || pathname?.startsWith(`${link.href}/`);
        return (
          <Link
            key={link.href}
            href={link.href}
            aria-current={active ? "page" : undefined}
            className={`rounded-[4px] px-2.5 py-1 text-[length:var(--text-sm)]
              transition-colors duration-[var(--duration)] ease-[var(--ease)]
              ${
                active
                  ? "bg-[var(--brand-tint)] font-medium text-[var(--brand)]"
                  : "text-ink-muted hover:bg-sunken hover:text-ink"
              }`}
          >
            {link.label}
          </Link>
        );
      })}

      <div className="ml-auto flex items-center gap-3">
        {user ? (
          <>
            <span className="hidden text-[length:var(--text-xs)] text-ink-muted sm:inline">
              {user.email}
            </span>
            <span className="rounded-[4px] bg-sunken px-1.5 py-0.5 text-[length:var(--text-2xs)] font-medium text-ink-muted">
              {user.role}
            </span>
            <button
              onClick={() => {
                clearSession();
                router.push("/login");
              }}
              className="rounded-[4px] px-2 py-1 text-[length:var(--text-xs)] text-ink-muted transition-colors duration-[var(--duration)] hover:bg-sunken hover:text-ink"
            >
              Sign out
            </button>
          </>
        ) : (
          <Link
            href="/login"
            className="text-[length:var(--text-xs)] text-ink-muted hover:text-ink"
          >
            Sign in
          </Link>
        )}
      </div>
    </nav>
  );
}
