import { MockClient } from "./mock";
import { createAuth, LiveClient } from "./live";
import type { AirportClient } from "./types";
export const isLive = import.meta.env.VITE_API_MODE === "live";
export let configurationError = "";
export const auth = (() => {
  try {
    return isLive ? createAuth() : null;
  } catch (error) {
    configurationError = String(error);
    return null;
  }
})();
export const client: AirportClient = isLive
  ? new LiveClient(async () => {
      const {
        data: { session },
      } = await auth!.auth.getSession();
      if (!session) throw new Error("Sign in to access this session.");
      return session.access_token;
    })
  : new MockClient();
