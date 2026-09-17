from fftui.ffmpeg.capability_index import CapabilityIndex

import pytest

from ffgui.doc import QueueDocument


def test_cleared_subtitle_burn_in_survives_save_load(doc, tmp_path):
    assert doc.set_output_field(0, "subtitle_burn_in", "captions.srt") is None
    assert doc.set_output_field(0, "subtitle_burn_in", "") is None
    store = tmp_path / "store"
    doc.save(store)

    loaded = QueueDocument.load(CapabilityIndex.stub(), store)

    assert loaded.item(0).error is None
    assert loaded.item(0).job.outputs[0].subtitle_burn_in == ""
    assert loaded.item(0).job.outputs[0].path == "alpha_out.mp4"
    assert "-vf" not in loaded.argv(0)


@pytest.mark.parametrize(("text", "spec", "expression"), [
    ("h264_metadata=aud=insert", "v:0", "h264_metadata=aud=insert"),
    ("v:0=h264_metadata=aud=insert", "v:0", "h264_metadata=aud=insert"),
    ("a:0=aac_adtstoasc", "a:0", "aac_adtstoasc"),
    ("=h264_mp4toannexb", "v:0", "h264_mp4toannexb"),
])
def test_bsf_preserves_filter_options(doc, text, spec, expression):
    assert doc.set_bsf(0, text) is None

    argv = doc.argv(0)

    assert doc.item(0).job.outputs[0].bsf == {spec: expression}
    index = argv.index(f"-bsf:{spec}")
    assert argv[index + 1] == expression
