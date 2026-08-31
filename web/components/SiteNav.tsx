import Link from "next/link";

// Deliberately plain: the pages need to be reachable from one another, and nothing
// in Plan 3 asks for more than that.
const LINKS: [string, string][] = [
  ["/map", "Map"],
  ["/health", "Health"],
];

export function SiteNav() {
  return (
    <nav
      data-testid="site-nav"
      className="flex shrink-0 items-center gap-4 border-b bg-white px-4 py-2 text-sm"
    >
      <span className="font-semibold text-slate-800">Sentinel CCTV Registry</span>
      {LINKS.map(([href, label]) => (
        <Link
          key={href}
          href={href}
          className="text-slate-600 underline-offset-4 hover:text-slate-900 hover:underline"
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}
