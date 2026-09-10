# Third-party notes

`ffgui` itself is Apache-2.0 (see `LICENSE`). Its dependencies split into two
groups: libraries the app links/imports, and tools the user supplies. Only the
first group ships with or alongside this code.

## Bundled or linked dependencies

- **PySide6 (LGPLv3)** — the Qt bindings, consumed as a shared library via
  pip. No Qt source is modified or statically linked, so the LGPL's
  requirements reduce to: keep the Qt/PySide copyright and license notices
  intact (pip does this), and don't prevent users from swapping in their own
  PySide6 build (a plain `pip install` satisfies this). Packagers: if you ever
  freeze the app into a single binary, ship the LGPL license text with it and
  keep a relinking path (e.g. keep the Qt libraries as separate files rather
  than embedding them where they can't be replaced).
- **Lucide icons (ISC)** — the SVGs under `assets/icons/` are vendored from
  [Lucide](https://lucide.dev) (`lucide-static` v0.544.0; each file carries an
  `@license` header). The ISC license requires the copyright notice to travel
  with the files, so it is reproduced here:

  > ISC License
  >
  > Copyright (c) Lucide contributors
  >
  > Permission to use, copy, modify, and/or distribute this software for any
  > purpose with or without fee is hereby granted, provided that the above
  > copyright notice and this permission notice appear in all copies.
  >
  > THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
  > WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
  > MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
  > ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
  > WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
  > ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
  > OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

- **fftui (sibling project)** — the `Job` model / command builder this GUI is
  built on, pulled from `github.com/RoyCoding8/FFmpeg-TUI`. It currently
  carries no license metadata; license it (Apache-2.0 or MIT keeps the whole
  stack permissive) so downstream users inherit a clean chain.

Permissive support libraries (PyYAML, pytest, ruff — MIT) need no action
beyond their own distributed notices.

## User-supplied, never bundled

- **ffmpeg** is located on the user's machine at runtime (`FFMPEG_PATH` or
  `PATH`) and never shipped with this project, so its (GPL/LGPL) terms impose
  nothing on this distribution. If you ever bundle an ffmpeg binary with the
  app, that changes: check that build's license and ship its sources/notices.
