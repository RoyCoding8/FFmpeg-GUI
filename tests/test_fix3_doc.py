"""Gate: stream-copy codecs are never flagged invalid; a filter edit becomes
the chain the builder actually uses."""
from types import SimpleNamespace

from ffgui.doc import QueueDocument


def _muxer_cap():
    return SimpleNamespace(
        entry=lambda kind, name: object() if (kind, name) == ("muxer", "mp4") else None,
        filter_kind=lambda kind: "video")


def test_copy_codec_never_flagged_invalid(qapp, tmp_path, probe):
    d = QueueDocument(_muxer_cap(), prober=probe)
    d.add_files([str(tmp_path / "alpha.mp4")])
    assert d.set_codec(0, "video", "copy") is None


def test_unknown_codec_still_flagged(qapp, tmp_path, probe):
    d = QueueDocument(_muxer_cap(), prober=probe)
    d.add_files([str(tmp_path / "alpha.mp4")])
    assert d.set_codec(0, "video", "libfoo") is not None


def test_set_filters_becomes_effective_chain(doc):
    out = doc.items[0].job.outputs[0]
    out.video_filters.filters.append("scale=640:360")
    assert doc.set_filters(0, "video_filters", ["unsharp"]) is None
    assert out.video_filters.filters == []
    assert doc.items[0].job.video_filters.filters == ["unsharp"]


def test_set_filters_clears_stale_output_audio_chain(doc):
    out = doc.items[0].job.outputs[0]
    out.audio_filters.filters.append("loudnorm=I=-16")
    assert doc.set_filters(0, "audio_filters", ["volume=-6dB"]) is None
    assert out.audio_filters.filters == []
    assert doc.items[0].job.audio_filters.filters == ["volume=-6dB"]
