"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, Field, Input, Notice } from "@/components/ui";
import { API, type CurrentUser, setSession } from "@/lib/session";

const DEMO = [
  ["root@gujarat.gov.in", "super admin", "everything"],
  ["mun.admin@gujarat.gov.in", "dept admin", "writes its own department"],
  ["analyst@gujarat.gov.in", "analyst", "reads and exports, cannot write"],
  ["viewer@gujarat.gov.in", "viewer", "reads only"],
];

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
      setSession(accessToken, (await me.json()) as CurrentUser);
      router.push("/map");
      router.refresh();
    } catch {
      setError("Could not reach the registry. Is the API running on port 8000?");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-full items-center justify-center p-6">
      <div className="w-full max-w-[24rem]">
        <div className="mb-6">
          <h1 className="text-[length:var(--text-xl)] font-semibold text-ink">
            Sentinel CCTV Registry
          </h1>
          <p className="mt-1 text-[length:var(--text-sm)] text-ink-muted">
            Gujarat Police · Centralised registry and GIS foundation
          </p>
        </div>

        <form
          onSubmit={submit}
          className="space-y-4 rounded-[6px] border border-line bg-surface p-5"
        >
          <Field label="Email" required>
            <Input
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </Field>

          <Field label="Password" required>
            <Input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>

          {error && <Notice tone="error">{error}</Notice>}

          <Button type="submit" variant="primary" busy={busy} className="w-full justify-center">
            {busy ? "Signing in" : "Sign in"}
          </Button>
        </form>

        <div className="mt-5 rounded-[6px] border border-line bg-sunken p-4">
          <p className="mb-2 text-[length:var(--text-xs)] font-medium text-ink-muted">
            Demonstration accounts — password{" "}
            <span className="font-mono">Sentinel@2026</span>
          </p>
          <ul className="space-y-1">
            {DEMO.map(([address, role, can]) => (
              <li key={address} className="text-[length:var(--text-xs)]">
                <button
                  type="button"
                  onClick={() => {
                    setEmail(address);
                    setPassword("Sentinel@2026");
                  }}
                  className="font-mono text-[var(--brand)] underline-offset-2 hover:underline"
                >
                  {address}
                </button>
                <span className="text-ink-faint"> — {role}, {can}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </main>
  );
}
