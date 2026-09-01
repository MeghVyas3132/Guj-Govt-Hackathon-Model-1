"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { clearSession, useCurrentUser } from "@/lib/session";

// Links are filtered by scope, so a viewer is not shown a page that would 403.
// The API enforces the same rules independently -- hiding a link is a courtesy,
// never the control.
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

  const visible = LINKS.filter(
    (link) => !link.scope || user?.scopes.includes(link.scope),
  );

  return (
    <nav className="flex shrink-0 flex-wrap items-center gap-1 border-b bg-white px-4 py-2 text-sm">
      <Link href="/map" className="mr-3 font-semibold text-slate-900">
        Sentinel CCTV Registry
      </Link>

      {visible.map((link) => {
        const active = pathname === link.href || pathname?.startsWith(`${link.href}/`);
        return (
          <Link
            key={link.href}
            href={link.href}
            className={`rounded px-2 py-1 ${
              active
                ? "bg-slate-900 text-white"
                : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
            }`}
          >
            {link.label}
          </Link>
        );
      })}

      <div className="ml-auto flex items-center gap-3 text-xs text-slate-500">
        {user ? (
          <>
            <span>
              {user.email}
              <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 font-medium text-slate-700">
                {user.role}
              </span>
            </span>
            <button
              onClick={() => {
                clearSession();
                router.push("/login");
              }}
              className="rounded border px-2 py-1 hover:bg-slate-50"
            >
              Sign out
            </button>
          </>
        ) : (
          <Link href="/login" className="rounded border px-2 py-1">
            Sign in
          </Link>
        )}
      </div>
    </nav>
  );
}
