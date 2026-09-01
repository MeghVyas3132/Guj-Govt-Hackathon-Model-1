import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { RequireAuth } from "@/components/RequireAuth";
import { SiteNav } from "@/components/SiteNav";

import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Sentinel CCTV Registry",
  description: "Model 1 — Centralised CCTV Registry & GIS Foundation.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex h-dvh flex-col overflow-hidden">
        <SiteNav />
        {/* min-h-0 lets this shrink below its content so the map can fill it
            exactly and scrolling pages scroll here rather than on the body. */}
        <main className="min-h-0 flex-1 overflow-auto">
          <RequireAuth>{children}</RequireAuth>
        </main>
      </body>
    </html>
  );
}
