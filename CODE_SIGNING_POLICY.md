# Code signing policy

## Purpose

WRelay Windows release installers are built from this public repository and signed with Authenticode through SignPath Foundation. A signature is intended to identify the publisher and connect each installer to its reviewed source revision; it does not guarantee that the software is free of defects.

Free code signing provided by SignPath.io, certificate by SignPath Foundation.

## People and responsibilities

- Authors and reviewers: the WRelay repository maintainers. The primary maintainer is [EraserOVO](https://github.com/EraserOVO).
- Signing approver: [EraserOVO](https://github.com/EraserOVO), acting as the project release maintainer. Every production signing request requires manual approval.
- Changes to build scripts, GitHub Actions workflows, SignPath artifact configuration, and this policy receive the same review as application code. Pull requests from non-committers must be reviewed by a maintainer before merge.

All project members who can submit or approve signatures must use multi-factor authentication for GitHub and SignPath.

## Build and signing process

1. A published GitHub Release triggers a build on a GitHub-hosted Windows runner from the release tag.
2. The workflow builds the unsigned installer from the checked-in source and submits that artifact through SignPath's GitHub trusted build integration.
3. The project maintainer manually approves the signing request. SignPath applies an Authenticode signature with timestamping and returns the signed installer.
4. The workflow verifies the Windows signature, computes SHA-256 after signing, and uploads the signed installer and checksum to that GitHub Release.

Only release artifacts built from this repository are submitted for signing. Third-party binaries bundled with the installer retain their upstream signatures, if any; they are not re-signed as WRelay binaries.

## Privacy

WRelay is operated locally and does not transfer information to WRelay-controlled services. Network transfers occur only as described in the [privacy and local data notice](docs/PRIVACY.md), when the operator enables or requests those features. The release workflow sends build artifacts and build provenance to GitHub Actions and SignPath for signing; it does not include `.env.prod`, `data/`, or runtime user data.

## Reporting

Report suspected signing or release-integrity issues through [GitHub Security Advisories](https://github.com/EraserOVO/WRelay/security/advisories/new) or the repository's issue tracker.
