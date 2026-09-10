# ffgui

A native desktop GUI that constructs FFmpeg commands and exports runnable `.bat` / `.sh`
scripts. The app is a *compiler*: it never runs ffmpeg itself (except an optional
detached-terminal launch of the exported script). Python is not in the execution path.

An alternative to XMedia Recode built on the [ffTUI](https://github.com/RoyCoding8/FFmpeg-TUI)
core (`fftui`): the `Job` model, the argv `command_builder`, the probed `CapabilityIndex`,
and the option validator.

## Tiers

- **Basic** — normal conversion: add files, pick container/codec, trim, copy streams, export.
- **Advanced** — tuning: rate control, presets, filters, subtitles, chapters, two-pass.
- **Expert** — the raw AVOption surface of your own ffmpeg build, rendered generically from
  the probed capability index, 100 % coverage.

## Export

`.bat` (with `%` doubling, `setlocal DisableDelayedExpansion`, embedded sidecars via
`echo` blocks) and `.sh` (`shlex.quote`, heredoc sidecars), golden-file and execution
tested. Queue export as one script or N scripts. Provenance header in every script
(generator, ffmpeg version hash, UTC date, summary).

## Development

```sh
uv sync
uv run pytest tests/ -q
uv run ruff check src tests
```

See `THIRD_PARTY.md` for license notes (PySide6 LGPL, Lucide ISC; ffmpeg is user-supplied,
never bundled).
