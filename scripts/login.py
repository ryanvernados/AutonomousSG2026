"""Sign in privately to Supabase and obtain an access token for local Swagger testing.

Run interactively in your own terminal. This prints the short-lived user token so you can
paste it into Swagger; it never prints the password, refresh token or backend secret key.
"""
import getpass

import httpx

from backend.api.config import Settings


def main():
    config = Settings()
    if config.auth_mode != "supabase":
        raise SystemExit("Demo mode uses DEMO_TOKEN directly. This helper is for Supabase mode.")
    email = input("Simulator application email: ").strip()
    password = getpass.getpass("Simulator application password (hidden): ")
    try:
        with httpx.Client(timeout=15) as client:
            response = client.post(config.supabase_url.rstrip("/") + "/auth/v1/token",
                params={"grant_type": "password"}, headers={"apikey": config.supabase_publishable_key},
                json={"email": email, "password": password})
            password = ""
            if response.status_code != 200:
                raise SystemExit("Sign-in failed. Check your application email/password and email confirmation.")
            token = response.json().get("access_token")
            if not token:
                raise SystemExit("Supabase did not return an access token.")
            check = client.get("http://127.0.0.1:8000/api/v1/scenarios",
                               headers={"Authorization": "Bearer " + token})
            if check.status_code == 200:
                print("Local backend authentication verified. Accessible scenarios:", len(check.json()))
            else:
                print("Supabase sign-in succeeded. Local backend verification returned:", check.status_code)
            print("Paste this short-lived user token into Swagger's Authorize field:")
            print(token)
    except httpx.HTTPError:
        raise SystemExit("Connection unavailable. Check that Supabase and the local backend are reachable.") from None


if __name__ == "__main__":
    main()
