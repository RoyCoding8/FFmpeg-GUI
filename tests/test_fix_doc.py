"""Gate: bsf text without a spec targets v:0; segment format follows the output name."""


def test_bsf_without_spec_targets_first_video_stream(doc):
    assert doc.set_bsf(0, "h264_mp4toannexb") is None
    assert doc.items[0].job.outputs[0].bsf == {"v:0": "h264_mp4toannexb"}


def test_bsf_spec_form_unchanged(doc):
    assert doc.set_bsf(0, "a:0=aac_adtstoasc") is None
    assert doc.items[0].job.outputs[0].bsf == {"a:0": "aac_adtstoasc"}


def test_segment_format_follows_output_suffix(doc):
    assert doc.set_output_field(0, "path", "movie.mkv") is None
    assert doc.set_segment_enabled(0, "True") is None
    assert doc.items[0].job.outputs[0].segment_format == "mkv"


def test_segment_format_defaults_to_mp4_without_suffix(doc):
    assert doc.set_output_field(0, "path", "movie") is None
    assert doc.set_segment_enabled(0, "True") is None
    assert doc.items[0].job.outputs[0].segment_format == "mp4"
