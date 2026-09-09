# Contributing

This is a small experimental Python project. Start with the current roadmap and actual validation
record. Propose one measurable improvement per issue/PR and include tests reproducing the problem.

Development requires Python 3.11+, a local FFmpeg/FFprobe installation for integration tests, and
`python -m pip install -e ".[dev,audio]"`. Run pytest, the configured Ruff checks and the public-tree
guard before a pull request. A GPU and real model are not required for unit tests.

Do not submit personal footage, copyrighted tracks, model weights, executables, secrets, account
identifiers or private infrastructure names. Generate media in temporary directories during tests,
or document provenance and permission for any future licensed fixture.

Use typed interfaces, explicit exceptions and bounded subprocess/network operations. Model responses
are untrusted input. Preserve backwards compatibility with existing project databases and plans.
Explain how your change affects Windows, Linux, caches, frame rates, source audio and portability.

A report of successful Premiere import must state the Premiere version, source codec/FPS/VFR
properties, sequence settings and observed frame/audio alignment. A real model evaluation must state
runtime/model revisions, sampling settings and hardware. Keep test-double and hardware evidence separate.

Submit security-sensitive details through the repository's private reporting channel when configured,
not in a public issue. Contributions to project source are made under its MIT license; third-party
artifacts keep their own licenses.
