# Distribution

`需求交付控制台-0.1.0-web.zip` contains the application, portable skill plugin,
static assets, bilingual guides, synthetic UI screenshots, tests and packaging
tools. Extract it and run `launcher.py` with Python 3.9 or newer, or use the
included macOS start command. On first use, select a time range and projects
(the preceding seven days are preselected), then review historical candidates.

Verify the archive from this directory:

```bash
shasum -a 256 -c 需求交付控制台-0.1.0-web.zip.sha256
```

The archive excludes local databases, runtime tokens, real screenshots, chat
records and project-specific adapters. It is not a bundled Python runtime or
native MCP UI. See `README.md` or `README.en.md` inside for full usage.

Rebuild this archive with `python3 tools/package.py` before running the Python
test suite. Browser checks use synthetic data and never authorize or perform
production operations. Real Codex protocol navigation and automatic dispatch
still require separate verification.
