# Publishing this as a public GitHub repository

The source archive is prepared for publication; it does not itself create a GitHub repository,
set a remote, push commits or authorize a release. No personal identity or company infrastructure
has been added to the code or examples.

## Before the first commit

Keep only source, tests, documentation, configuration examples and GitHub workflow files.
Do not include your private media, songs, SQLite projects, screenshots of personal footage,
model weights, FFmpeg/llama.cpp binaries, local configs, logs, compiled environments or exports.
`work/`, `models/` and `tools/` are ignored. Original video/image extensions and databases are also
ignored. Local XML/JSON exports contain absolute filesystem paths and must stay private.

The code is supplied with an MIT license under a generic contributors notice. Review that choice
before publishing; the project does not own or relicense third-party models, applications or media.
The working title/package name is provisional; availability has not been checked or reserved.

```bash
git init -b main
git add .
python scripts/check_public_tree.py
git diff --cached --stat
git diff --cached
```

Inspect the actual staged contents, not only the ignore file. A positive guard result is a useful
check, not a replacement for review or a dedicated secret scanner. Commit under your own configured
Git identity; no identity or email is configured by this repository.

Create the intended public repository under the intended GitHub identity and add its actual remote
URL yourself. This project never guesses which personal or business account should receive it.
Enable branch protection, reviewed pull requests, dependency/security alerts and private vulnerability
reporting as appropriate. Run CI once the repository exists; a included workflow is not a CI result.

## How to describe the first release honestly

Suggested description:

> Experimental local-first footage indexer and rough-cut planner. Preserves original media,
> uses a per-project SQLite catalog, and exports editable legacy XML for Premiere workflows.
> Local vision support is under hardware and real-footage validation.

Do not claim Filmora parity, an automatic cinematic editor, 70-90% time savings, guaranteed RTX
performance, precise action recognition or Premiere certification without measured evidence.

## Contribution workflow

Use small issues tied to the roadmap gates. A useful first contribution is a reproducible import
report with synthetic or explicitly licensed media. Contributors must not upload copyrighted songs,
private footage or logs containing sensitive paths. Keep dependencies and GitHub Actions reviewed;
changing a model/runtime can invalidate analysis reproducibility.
