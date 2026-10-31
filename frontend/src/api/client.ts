import { MockClient } from "./mock";
import { createAuth, LiveClient } from "./live";
import type { AirportClient } from "./types";
export const isLive = import.meta.env.VITE_API_MODE === "live";
// Loopback development only: Member 1's AUTH_MODE=demo accepts its shared DEMO_TOKEN
// instead of a Supabase session. Never set VITE_AUTH_MODE=demo for a hosted build.
export const demoToken: string =
  isLive && import.meta.env.VITE_AUTH_MODE === "demo"
    ? (import.meta.env.VITE_DEMO_TOKEN ?? "")
    : "";
export let configurationError =
  isLive && import.meta.env.VITE_AUTH_MODE === "demo" && !demoToken
    ? "VITE_AUTH_MODE=demo requires VITE_DEMO_TOKEN."
    : "";
export const auth = (() => {
  try {
    return isLive && !demoToken ? createAuth() : null;
  } catch (error) {
    configurationError = String(error);
    return null;
  }
})();
export const client: AirportClient = isLive
  ? new LiveClient(demoToken ? async () => demoToken : async () => {
      const {
        data: { session },
      } = await auth!.auth.getSession();
      if (!session) throw new Error("Sign in to access this session.");
      return session.access_token;
    })
  : new MockClient();
