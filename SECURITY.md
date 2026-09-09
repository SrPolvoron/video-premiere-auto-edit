# Security policy

This is an alpha and has not received an independent security audit. Do not treat an untrusted
repository checkout, model runtime, project database or media file as safe merely because processing
is local. Keep FFmpeg, Python, Pillow, dependencies, drivers and inference binaries updated.

## Boundaries

The application executes FFmpeg and the explicitly configured llama.cpp binary using argument lists,
not shell strings. Model JSON is validated against bounded fields; it cannot issue tool or shell
commands. Vision requests go only to literal loopback addresses. Redirects and environment proxies
are disabled in the client. No API key is needed and the application implements no telemetry.

The model helper binds to loopback, not the LAN. Localhost is not authentication: another process
running as the same user may access it. Close the helper after use; the managed mode does that on
normal exit, errors and interruption. A forcibly killed parent or machine failure can leave child
processes behind; inspect running processes before removing a stale project lock.

Original media is read-only to application operations. Cache cleanup is restricted to the project's
derived cache directory. One explicit writer lock avoids concurrent project mutation. SQLite uses
foreign keys and transactions, but sudden unplugging or filesystem failure can still corrupt data.
Maintain a separate backup and safely eject external drives after all processes close.

## Sensitive artifacts

Project databases, thumbnails, logs, exported XML/JSON and prompts can contain sensitive information.
They are not encrypted by this application. Protect the storage volume as appropriate and never add
such artifacts to public issues or commits. Ignore rules and the public-tree guard are defense-in-depth,
not an exhaustive DLP system. The guard is not a full dependency-vulnerability scanner.

## Reporting

For the future hosted repository, use GitHub private vulnerability reporting when enabled by its
maintainer. Until that channel exists, do not post exploit details or personal media publicly.
No maintainer email or response-time promise is fabricated in this template.
