"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { useToken } from "@/lib/session";

/**
 * Sends signed-out visitors to /login.
 *
 * A convenience, not a control: every endpoint enforces its own scope, so someone
 * who bypasses this sees a page whose requests all fail rather than data they
 * should not have. It also reacts to the session being cleared by a 401, so an
 * expired token moves the user to the login screen instead of leaving them on a
 * page that silently shows nothing.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const token = useToken();
  const isLogin = pathname?.startsWith("/login") ?? false;

  useEffect(() => {
    // Only null redirects. undefined means the store has not hydrated yet, and
    // treating that as signed-out bounces every hard page load to /login.
    if (!isLogin && token === null) {
      router.replace("/login");
    }
  }, [isLogin, token, router]);

  if (!isLogin && token !== undefined && token === null) return null;
  return <>{children}</>;
}
