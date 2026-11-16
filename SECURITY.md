# Security policy

wigix runs on your desktop or server, talks to your services and (optionally) runs commands for tap actions, so
security reports are welcome.

**Please report vulnerabilities privately** through GitHub's "Report a vulnerability" (Security tab) rather than a public
issue. Include what an attacker needs, what they get, and a minimal reproduction. You will get a reply within a few days.

## Model

- The web editor is a local administration tool. It is protected by a secret token, a Host allow-list, a custom header on
  writes and a strict CSP, and it never changes `plugin_dirs`, `security` or `editor`. Anyone holding the access link
  can still change which services the daemon talks to: keep it on localhost.
- Commands, key presses and typed text from tap actions are disabled unless `security: {allow_commands: true}` is set in
  the file by hand.
- Secrets belong in files referenced with `*_file:` keys (mode 0600), not inline in the YAML.

## Supported versions

Only the latest release.
