# Security Policy

PotionUI is alpha software maintained by one person in their spare time. This
policy is deliberately short: it says how to report a problem, what's in
scope, and what to expect — not an enterprise SLA.

## Supported versions

Only the current `0.0.x` line is supported. This is pre-release software with
no backport policy — fixes land on the latest release, not on older tags.

## Reporting a vulnerability

Please report security issues privately through GitHub's [Security
Advisories](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
feature on this repository ("Security" tab → "Report a vulnerability"), not as
a public issue or Discord message.

There's no dedicated security email and no formal SLA. This is a best-effort,
solo-maintained project — expect an initial response typically within a week,
not guaranteed, and no bug bounty.

## Scope

**In scope** — the kind of thing worth a private report:

- Authentication or authorization bypass (reaching an endpoint or resource
  without valid credentials, or as the wrong user).
- Cross-user data access — one account reading or modifying another
  account's sessions, generations, presets, or settings.
- Path traversal in file serving or media/upload handling.
- Credential leakage — provider/backend API keys or secrets appearing in
  logs, API responses, or the admin UI where they shouldn't.
- Server-side request forgery (SSRF) in model downloads or other
  server-initiated fetches.
- Vulnerabilities in the code PotionUI ships (core, `src/plugin_api/`
  consumers, and code under `vendor/`), as opposed to a model or plugin you
  chose to install from elsewhere.

**Expected deployment reality, not a vulnerability report:**

- PotionUI is designed to run on hardware you control — a workstation or a
  trusted LAN — behind your own authenticating reverse proxy if you expose it
  further. It is not hardened as an internet-facing multi-tenant service, and
  running it that way without your own access controls in front of it is a
  deployment choice, not a PotionUI bug.
- Generation runs in-process on your own GPU/host. There is no sandbox
  between a generation job and the rest of the machine PotionUI runs on —
  treat it like any other tool you run locally.
- Plugins (`content/plugins/marketplace/` and `content/plugins/local/`) are
  Python and frontend code that runs with the same privileges as the app
  itself — there's no plugin sandbox. Installing a plugin from a source you
  don't trust is equivalent to running any other untrusted code on your
  machine; that risk is yours to manage, not something core can contain.
- Licensing or legality questions about a model you downloaded, or content
  you generated with it — see the **Models** section of the
  [README](README.md). Not a security report.

## Hardening notes for operators

- The backend binds to `127.0.0.1` by default; it only listens on the
  network when you explicitly opt in (`./run.sh --lan`, or
  `POTIONUI_HOST=0.0.0.0`). If you do expose it beyond localhost, put an
  authenticating reverse proxy in front of it — PotionUI's own login is not
  designed to be your only line of defense on an open network.
- Only install plugins from sources you trust — a plugin is unsandboxed code
  that runs as PotionUI.
- Provider and backend credentials live per-provider in the database
  (Admin → Plugins, not a global API-key setting) and are encrypted at rest
  as of migration 111. The encryption key — `POTIONUI_SECRET_KEY`, or the
  key file generated next to your database — is what actually protects those
  values; losing it makes stored credentials unrecoverable, and anyone who
  gets it can decrypt them, so back it up separately and keep it out of
  anything you'd share (repos, unencrypted backups, support bundles).
- Keep Python and frontend dependencies up to date; PotionUI doesn't
  currently run automated dependency-vulnerability scanning.

Thanks for helping keep PotionUI and the people running it safe.
