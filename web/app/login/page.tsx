"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { API, type CurrentUser, setSession } from "@/lib/session";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`${API}/api/v1/auth/login`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!response.ok) {
        setError("Incorrect email or password.");
        return;
      }
      const { access_token: accessToken } = await response.json();

      const me = await fetch(`${API}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      const user: CurrentUser = await me.json();

      setSession(accessToken, user);
      router.push("/map");
      router.refresh();
    } catch {
      setError("Could not reach the registry.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-full items-center justify-center bg-slate-50 p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-lg border bg-white p-6 shadow-sm"
      >
        <h1 className="text-lg font-semibold">Sentinel CCTV Registry</h1>
        <p className="mb-6 text-xs text-slate-500">
          Gujarat Police · Model 1 — Registry &amp; GIS Foundation
        </p>

        <label className="mb-3 block">
          <span className="mb-1 block text-xs font-semibold uppercase text-slate-500">
            Email
          </span>
          <input
            className="w-full rounded border px-3 py-2 text-sm"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>

        <label className="mb-4 block">
          <span className="mb-1 block text-xs font-semibold uppercase text-slate-500">
            Password
          </span>
          <input
            className="w-full rounded border px-3 py-2 text-sm"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {error && (
          <p role="alert" className="mb-3 text-sm text-red-600">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded bg-slate-900 py-2 text-sm font-medium text-white disabled:opacity-40"
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <p className="mt-5 border-t pt-4 text-xs text-slate-400">
          Demo accounts — password <code>Sentinel@2026</code>
          <br />
          root@ · mun.admin@ · analyst@ · viewer@ (gujarat.gov.in)
        </p>
      </form>
    </main>
  );
}
