# Security policy

## Supported versions

Only the latest release receives fixes. Installed copies update themselves, so a fix reaches
users as soon as it is published.

| Version | Supported |
|---|---|
| Latest release | Yes |
| Anything older | No, update from inside the app or from [refrata.vercel.app](https://refrata.vercel.app) |

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately through **[Security › Report a vulnerability](https://github.com/LucsLabsStudios/refrata/security/advisories/new)**
on this repository. Include the Refrata version (**Settings › About**), your operating system, the
steps to reproduce, and what an attacker could achieve.

You will get an acknowledgement within a few days. Once a fix ships, the advisory is published
and the report is credited unless you prefer otherwise.

## How updates are protected

- Every release file is published with its SHA-256 checksum and an Ed25519 signature.
- The in-app updater downloads only from this repository's GitHub releases, and installs a file
  only when both its checksum and its signature check out against the public key built into the
  app. A compromised GitHub account alone cannot push an update to existing users.
- yt-dlp self-updates come only from PyPI over HTTPS and are checked against PyPI's SHA-256.
- The bundled libVLC is the official VideoLAN build, verified against VideoLAN's published
  SHA-256 at build time.

## Scope

In scope: the Refrata application, its installer and packages, the update mechanism, and the
build and release tooling in this repository.

Out of scope: vulnerabilities in libVLC, Qt or yt-dlp themselves (report those upstream; Refrata
picks up their fixes in the next release), and the website's hosting platform.
