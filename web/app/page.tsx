import { redirect } from "next/navigation";

/**
 * The registry has no separate landing page: the map is the home screen, which
 * is where the nav logo and a successful sign-in both point. Redirecting keeps
 * one answer to "where does an operator start" rather than a second dashboard
 * to maintain, and RequireAuth sends signed-out visitors on to /login.
 */
export default function Home() {
  redirect("/map");
}
