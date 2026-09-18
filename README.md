<p align="center">
  <img src="assets/ffgui-logo.svg" alt="ffgui logo" width="96">
</p>

<h1 align="center">ffgui</h1>

<p align="center">
  A native desktop GUI for building FFmpeg commands and exporting runnable scripts.
</p>

<p align="center">
  <a href="https://github.com/RoyCoding8/FFmpeg-GUI/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/RoyCoding8/FFmpeg-GUI/ci.yml?branch=main&amp;label=CI&amp;logo=github&amp;style=for-the-badge"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&amp;logoColor=white&amp;style=for-the-badge">
  <img alt="PySide6" src="https://img.shields.io/badge/PySide6-6.8%2B-41CD52?logo=qt&amp;logoColor=white&amp;style=for-the-badge">
  <a href="LICENSE"><img alt="Apache 2.0 license" src="https://img.shields.io/badge/license-Apache--2.0-2563EB?style=for-the-badge"></a>
</p>

<p align="center">
  <a href="#get-started"><img alt="Get started" src="https://img.shields.io/badge/Get_started-2563EB?style=for-the-badge"></a>
  <a href="https://github.com/RoyCoding8/FFmpeg-GUI"><img alt="View source" src="https://img.shields.io/badge/View_source-1C1C1A?logo=github&amp;logoColor=white&amp;style=for-the-badge"></a>
  <a href="https://github.com/RoyCoding8/FFmpeg-GUI/issues"><img alt="Report an issue" src="https://img.shields.io/badge/Report_an_issue-DC2626?style=for-the-badge"></a>
</p>

ffgui is a PySide6 desktop application that builds FFmpeg commands from a media queue and exports runnable `.bat` and `.sh` scripts. It does not run FFmpeg during normal editing. The optional **Run in terminal** action launches an exported script in a detached terminal.

> **Bring your own FFmpeg.** ffgui discovers the FFmpeg build you provide or lets you locate it. FFmpeg is never bundled with this project.

## What you can do

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="assets/icons/film.svg" alt="" width="24" height="24">
      <br><strong>Basic</strong>
      <br>Add media, choose a container and codecs, trim clips, copy streams, and export a script.
    </td>
    <td width="50%" valign="top">
      <img src="assets/icons/settings.svg" alt="" width="24" height="24">
      <br><strong>Advanced</strong>
      <br>Set rate control, presets, filters, subtitles, chapters, and two-pass encoding options.
    </td>
  </tr>
  <tr>
    <td width="50%" valign="top">
      <img src="assets/icons/folder.svg" alt="" width="24" height="24">
      <br><strong>Expert</strong>
      <br>Work with the AVOptions exposed by the FFmpeg build detected on your machine.
    </td>
    <td width="50%" valign="top">
      <img src="assets/icons/file-text.svg" alt="" width="24" height="24">
      <br><strong>Review before export</strong>
      <br>Inspect the generated command, choose one script per queue or one script for the full queue, and keep provenance in the exported file.
    </td>
  </tr>
</table>

## See the workflow

The workspace combines a presets sidebar, media queue, configuration tabs, command preview, and export controls in one window.

<table>
  <tr>
    <td><img src="assets/screenshots/shell-light.png" alt="ffgui light theme with an empty media queue"></td>
    <td><img src="assets/screenshots/shell-dark.png" alt="ffgui dark theme with an empty media queue"></td>
  </tr>
  <tr>
    <td colspan="2" align="center"><em>Light and dark themes</em></td>
  </tr>
</table>

## How it works

1. Add files or folders to the media queue.
2. Choose a workflow tier and set the options you need.
3. Review the generated FFmpeg command.
4. Export a Windows batch script or a POSIX shell script.

The export layer quotes arguments for each shell, embeds sidecar files when needed, and records the generator, FFmpeg version hash, UTC date, and job summary in the script header.

## Get started

Install Python 3.12 or newer, `uv`, and an FFmpeg build. Make FFmpeg available to ffgui through your system path or the application's file picker.

```sh
uv sync
uv run ffgui
```

On Windows, `run.bat` wraps the same command. On Linux and macOS, `run.sh` does the same.

## Development

```sh
uv run pytest tests/ -q
uv run ruff check src tests
uv run python scripts/check_readme.py
```

The project uses the `fftui` core for the `Job` model, command builder, capability index, and option validator.

## License

ffgui is available under the [Apache License 2.0](LICENSE). See [THIRD_PARTY.md](THIRD_PARTY.md) for PySide6, Lucide, PyYAML, pytest, Ruff, and FFmpeg notices.
