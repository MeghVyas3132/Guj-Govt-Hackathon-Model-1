"use client";

/**
 * A preview of what a camera is actually seeing.
 *
 * Every request — manifest, segments, AES key — goes through the registry's own
 * proxy, because the gateway sends no CORS headers and its session cookie is
 * HttpOnly and SameSite=Lax. Playing the stream directly from this origin is not
 * merely inconvenient; the browser will not do it.
 *
 * Loaded on demand rather than on mount. A page of camera cards that each opened
 * a video stream would pull megabytes nobody asked for, so the poster state is a
 * button and playback begins when someone asks for it.
 */

import Hls from "hls.js";
import { useEffect, useRef, useState } from "react";

import { Button, Notice } from "@/components/ui";
import { API, getToken } from "@/lib/session";

type State = "idle" | "loading" | "playing" | "error";

export function CameraPlayer({
  cameraId,
  label,
}: {
  cameraId: string;
  label?: string;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const hls = useRef<Hls | null>(null);
  const [state, setState] = useState<State>("idle");
  const [error, setError] = useState<string | null>(null);

  // Tear down on unmount or when the camera changes. Without this, navigating
  // between cameras leaves the previous stream downloading in the background.
  useEffect(() => {
    return () => {
      hls.current?.destroy();
      hls.current = null;
    };
  }, [cameraId]);

  function start() {
    const element = video.current;
    if (!element) return;

    setState("loading");
    setError(null);

    const src = `${API}/api/v1/cameras/${cameraId}/preview.m3u8`;
    const token = getToken();

    if (!Hls.isSupported()) {
      // Safari plays HLS natively, but native playback cannot attach an
      // Authorization header, so this path only works for an unauthenticated
      // proxy. Stated rather than failing silently.
      setState("error");
      setError(
        "This browser has no MediaSource support. Chrome, Edge or Firefox can play this preview.",
      );
      return;
    }

    const instance = new Hls({
      // Every request the player makes goes to our proxy and must carry the
      // bearer token: the manifest, each segment, and the decryption key.
      xhrSetup: (xhr) => {
        if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      },
      // The sandbox archives are 12 hours long. Buffering ahead aggressively
      // would download hundreds of megabytes for a glance at a preview.
      maxBufferLength: 12,
      maxMaxBufferLength: 30,
    });

    instance.on(Hls.Events.ERROR, (_event, data) => {
      if (!data.fatal) return;
      // A fatal network error here is almost always the upstream session having
      // expired, which the proxy reports as a 502 with that wording.
      setState("error");
      setError(
        data.response?.code === 502
          ? "The camera gateway refused the request — its session may have expired."
          : `Playback failed (${data.details}).`,
      );
      instance.destroy();
      hls.current = null;
    });

    instance.on(Hls.Events.MANIFEST_PARSED, () => {
      void element.play().catch(() => {
        // Autoplay policy can refuse; the controls are visible either way.
      });
      setState("playing");
    });

    instance.loadSource(src);
    instance.attachMedia(element);
    hls.current = instance;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="relative aspect-video overflow-hidden rounded-[6px] border border-line bg-[oklch(0.22_0.01_250)]">
        <video
          ref={video}
          controls={state === "playing"}
          muted
          playsInline
          className="h-full w-full object-contain"
        />

        {state !== "playing" && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-4 text-center">
            {state === "idle" && (
              <>
                <p className="text-[length:var(--text-xs)] text-[oklch(0.72_0.01_250)]">
                  {label ? `Preview ${label}` : "Preview this camera"}
                </p>
                <Button onClick={start}>Play preview</Button>
              </>
            )}
            {state === "loading" && (
              <p className="text-[length:var(--text-xs)] text-[oklch(0.72_0.01_250)]">
                Connecting to the gateway…
              </p>
            )}
            {state === "error" && (
              <Button variant="default" onClick={start}>
                Retry
              </Button>
            )}
          </div>
        )}
      </div>

      {error && <Notice tone="warn">{error}</Notice>}

      <p className="text-[length:var(--text-2xs)] text-ink-faint">
        Relayed through the registry because the gateway sends no CORS headers.
        Preview only — the registry does not record or restream.
      </p>
    </div>
  );
}
