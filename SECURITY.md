# Security Policy

## Supported versions

Security fixes are currently applied to the latest state of the default branch.

## Reporting a vulnerability

Please do not open a public GitHub issue for suspected vulnerabilities.

Send a private report to `security@ailouros.io` with:

- a short description of the issue
- impact and affected area
- reproduction steps or a proof of concept
- any suggested mitigation, if you have one

We will acknowledge receipt as soon as practical, investigate the issue, and
coordinate disclosure once a fix or mitigation is available.

## Security notes for operators

- Keep `.env` files local and never commit them
- Treat `SWARM_ALLOW_COMMAND_EXEC=1` and `SWARM_ALLOW_WORKSPACE_WRITE=1` as high-risk settings
- Do not expose the local API to untrusted networks when privileged agent actions are enabled
- Rotate any cloud provider keys immediately if you suspect accidental disclosure
