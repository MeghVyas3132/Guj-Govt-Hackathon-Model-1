"use client";

/**
 * Session state, exposed as an external store.
 *
 * localStorage is an external system, so components read it through
 * useSyncExternalStore rather than copying it into state inside an effect. That
 * keeps server and client render agreeing (the server snapshot is always null)
 * and avoids the cascading re-render an effect-plus-setState causes.
 *
 * The token lives in localStorage because this is a demonstration portal and the
 * API is a separate origin. Behind a single domain it should move to an httpOnly,
 * SameSite cookie so a script injection cannot read it -- a deployment change, not
 * a rewrite, because only this file knows where the token is kept.
 */

import { useSyncExternalStore } from "react";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const TOKEN_KEY = "sentinel.access_token";
const USER_KEY = "sentinel.user";

export type CurrentUser = {
  id: string;
  email: string;
  full_name: string;
  role: string;
  department_id: string | null;
  department_code: string | null;
  scopes: string[];
};

const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab signing out should sign this one out too.
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

// Cached so getSnapshot returns a stable reference; returning a fresh object each
// call makes useSyncExternalStore loop.
let cachedRaw: string | null = null;
let cachedUser: CurrentUser | null = null;

function userSnapshot(): CurrentUser | null {
  const raw = read(USER_KEY);
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    try {
      cachedUser = raw ? (JSON.parse(raw) as CurrentUser) : null;
    } catch {
      cachedUser = null;
    }
  }
  return cachedUser;
}

// undefined means "not hydrated yet", distinct from null meaning "no session".
// Without that distinction every hard page load looks signed-out for one render
// and RequireAuth bounces the user to /login.
const serverSnapshot = () => undefined;

export function useCurrentUser(): CurrentUser | null | undefined {
  return useSyncExternalStore(subscribe, userSnapshot, serverSnapshot);
}

export function useToken(): string | null | undefined {
  return useSyncExternalStore(subscribe, () => read(TOKEN_KEY), serverSnapshot);
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return read(TOKEN_KEY);
}

export function getUser(): CurrentUser | null {
  if (typeof window === "undefined") return null;
  return userSnapshot();
}

export function setSession(token: string, user: CurrentUser): void {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
  emit();
}

export function clearSession(): void {
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(USER_KEY);
  emit();
}

export function can(user: CurrentUser | null, scope: string): boolean {
  return user?.scopes.includes(scope) ?? false;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body?: unknown,
  ) {
    super(message);
  }
}

/**
 * Fetch with the bearer token attached.
 *
 * On a 401 the session is cleared and subscribers are notified; RequireAuth then
 * routes to /login through the Next router. Navigating from here with
 * window.location would bypass the router and full-reload the app.
 */
export async function apiFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const token = getToken();
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      ...(init.headers ?? {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });

  if (response.status === 401 && typeof window !== "undefined") {
    clearSession();
  }
  return response;
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(path, init);
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : ((detail as { message?: string } | undefined)?.message ??
          `Request failed (${response.status})`);
    throw new ApiError(response.status, message, body);
  }
  return body as T;
}

export async function postJson<T>(path: string, payload: unknown): Promise<T> {
  return apiJson<T>(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}
