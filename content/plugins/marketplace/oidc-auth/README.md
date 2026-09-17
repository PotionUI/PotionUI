# OIDC Login

A generic OpenID Connect login provider for PotionUI. Point it at any OIDC issuer that
publishes standard discovery and JWKS documents - Authentik, Keycloak, Zitadel, Auth0,
Microsoft Entra ID, or anything else that speaks the protocol - and a "Continue with
{label}" button appears on the login page.

The plugin owns the OIDC protocol (discovery, PKCE, state/nonce, ID token verification,
UserInfo enrichment). Core owns the mapping from a verified external identity onto a
PotionUI user, so groups, model access, preset access and per-user resources all work
exactly as they do for a local account.

## What it does

1. `GET /api/plugins/oidc-auth/start` redirects the browser to your issuer's
   authorization endpoint with a PKCE (S256) challenge, `state` and `nonce`. It stores
   the flow's secrets (state, nonce, PKCE verifier, redirect URI) in a short-lived
   (10 minute), HttpOnly, signed cookie - nothing is kept server-side.
2. Your issuer authenticates the user and redirects back to
   `GET /api/plugins/oidc-auth/callback` with an authorization code.
3. The plugin exchanges the code at the token endpoint (with the PKCE verifier),
   verifies the returned ID token's signature against the issuer's JWKS, `iss`, `aud`
   (and `azp` when the token has more than one audience), `exp` and `nonce`, then fills
   in any of `email` / `preferred_username` / `name` the ID token didn't carry from the
   UserInfo endpoint.
4. It hands the verified `(issuer, sub)` pair and claims to core's login-provider API,
   which signs the user in (or creates/links an account, per the settings below) and
   redirects the browser back into PotionUI.

Every failure - a bad signature, a state/nonce/audience mismatch, an expired token, an
issuer that rejects PKCE, whatever - lands on `/login?error=external_login` with the
reason only in the server log, never in the URL or the response body.

## Settings

Configure these in Admin -> Plugins -> OIDC Login.

| Setting | Required | Description |
|---|---|---|
| Issuer URL | yes | The OIDC issuer, e.g. `https://auth.example.com/application/o/potionui/`. Discovery is fetched from `<issuer>/.well-known/openid-configuration`, and that document's own `issuer` field must match this value exactly (trailing slash aside) - a mismatch is rejected as a misconfigured or spoofed issuer. Must be `https://`, unless the host is `localhost`/`127.0.0.1`/`::1` for local testing - the plugin treats itself as unconfigured otherwise, the same as a missing setting. |
| Client ID | yes | The client id PotionUI was registered with at the issuer. |
| Client Secret | yes | The client secret PotionUI was registered with at the issuer. Stored encrypted. |
| Scopes | no | Space-separated OAuth2 scopes. Defaults to `openid profile email`. |
| Button Label | no | Text on the login page button, shown as "Continue with `<label>`". Defaults to `SSO`. Changing this takes effect after PotionUI restarts (or the plugin is disabled and re-enabled) - it is only read when the button is registered, not per-request. |
| Redirect URI Override | no | Overrides the callback URL sent to the issuer. Leave blank to derive it from the incoming request (honouring `X-Forwarded-Proto`/`X-Forwarded-Host`); **set this explicitly in any deployment behind a reverse proxy** rather than relying on those headers being forwarded correctly. Whatever value is in effect must be registered at the issuer as an exact-match redirect URI - never a wildcard or prefix match, since the issuer's own redirect-URI check is what stops an attacker from redirecting the authorization code somewhere else. |

### Redirect URI to register at your issuer

Register this exact URL as an allowed redirect URI for the client:

```
https://<your-potionui-host>/api/plugins/oidc-auth/callback
```

### What the core admin settings control

Three SYSTEM settings, in Administration -> System Settings -> External login, govern
what happens once this plugin hands core a verified identity - they are not specific to
this plugin and apply to every login-provider plugin:

| Setting | Default | Effect |
|---|---|---|
| `external_login_auto_create` | off | Whether a first-time sign-in from this issuer may create a local PotionUI account. Off means an unmapped identity is refused. |
| `external_login_default_group` | none | Group a user created this way is added to. |
| `external_login_link_by_email` | off | Whether a verified email from the issuer (`email_verified: true`) may attach to an existing local account instead of refusing or creating a new one. |

Local username/password login is untouched by any of this, and the instance owner
account can always sign in locally - an issuer outage never locks you out.

## Setup: Authentik

1. In Authentik, create an **OAuth2/OpenID Provider**: Applications -> Providers ->
   Create. Set **Redirect URIs** to the callback URL above, and note the generated
   **Client ID** and **Client Secret**.
2. Create an **Application** using that provider, and add whichever users/groups should
   be able to sign in.
3. Authentik's discovery document lives at
   `https://<authentik-host>/application/o/<application-slug>/.well-known/openid-configuration`.
   Set **Issuer URL** in PotionUI to
   `https://<authentik-host>/application/o/<application-slug>/` (the URL discovery is
   served *relative to*, not the discovery URL itself - Authentik's `issuer` claim is the
   slug URL without the `.well-known` suffix).
4. Fill in Client ID and Client Secret from step 1, save, and the button appears on the
   PotionUI login page after a restart (or disable/re-enable the plugin).

## Setup: Keycloak

1. In the Keycloak admin console, open your realm and create a **Client**: Clients ->
   Create client. Client type **OpenID Connect**, enable **Client authentication**
   (confidential client), and set **Valid redirect URIs** to the callback URL above.
2. On the **Credentials** tab, copy the **Client secret**.
3. Keycloak's discovery document lives at
   `https://<keycloak-host>/realms/<realm>/.well-known/openid-configuration`. Set
   **Issuer URL** in PotionUI to `https://<keycloak-host>/realms/<realm>` - that is
   exactly the `issuer` claim Keycloak's discovery document reports.
4. Fill in Client ID and Client Secret from steps 1-2, save.

The same shape works for Zitadel, Auth0 and Entra ID: find the issuer discovery lists
under a `.well-known/openid-configuration` path, use the base issuer URL (not the
discovery URL) as **Issuer URL**, and register the callback URL above as the redirect
URI.

## Troubleshooting

- **Button doesn't appear on the login page.** The plugin registers the button when it
  boots, regardless of whether Issuer URL/Client ID/Client Secret are filled in yet -
  if it's still missing, confirm the plugin is enabled in Admin -> Plugins and check the
  server log for a mount failure.
- **Clicking the button lands on `/login?error=external_login` immediately.** Issuer
  URL, Client ID or Client Secret is empty, or discovery failed - check the server log
  for the specific reason (never shown in the URL, by design).
- **`discovery document issuer ... does not match configured issuer ...`.** The Issuer
  URL setting has to be exactly what the issuer's own discovery document reports back as
  its `issuer` claim - a trailing slash is tolerated, a different path or host is not.
  Copy the value from the discovery JSON itself rather than guessing it.
- **Sign-in reaches the issuer and comes back to `/login?error=external_login`.** Most
  often either the issuer's `redirect_uri` doesn't match what PotionUI sent (check
  Redirect URI Override, or whether a reverse proxy is stripping
  `X-Forwarded-Proto`/`X-Forwarded-Host`), or the user has no existing mapping and
  `external_login_auto_create` is off. Both cases are logged server-side with the exact
  reason.
- **A previously-working sign-in starts failing after rotating the issuer's signing
  keys.** The plugin refetches the issuer's JWKS once when it sees a `kid` it doesn't
  recognize, then rate-limits further refetches for 30 seconds - a single rotation
  should self-heal within one failed attempt. Repeated failures point at a JWKS endpoint
  that's unreachable or serving something unexpected.
- **Changed the Button Label and it didn't change.** The label is only read when the
  button is registered (plugin boot / enable), not per-request - restart PotionUI or
  disable and re-enable the plugin.
