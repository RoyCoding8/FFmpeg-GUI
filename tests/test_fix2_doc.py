"""Gate: faststart converges legacy movflags siblings; probe-error rows keep
their warning across save/load."""
import pytest
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Input, InputStream

from ffgui.doc import QueueDocument


@pytest.fixture()
def probe():
    return lambda path: Input(path=str(path), streams=[
        InputStream(input_index=0, spec="v:0", codec_type="video",
                    codec_name="h264", width=64, height=48)])


@pytest.fixture()
def doc(qapp, tmp_path, probe):
    d = QueueDocument(CapabilityIndex.stub(), prober=probe)
    d.add_files([str(tmp_path / "alpha.mp4")])
    return d


def test_faststart_merges_legacy_sibling_flags(doc):
    out = doc.items[0].job.outputs[0]
    out.video_options["movflags"] = "+faststart+nomobsync"
    assert doc.set_faststart(0, "True") is None
    assert out.video_options == {}
    assert out.options["movflags"] == "+faststart+nomobsync"


def test_faststart_off_keeps_legacy_siblings(doc):
    out = doc.items[0].job.outputs[0]
    out.video_options["movflags"] = "+faststart+nomobsync"
    assert doc.set_faststart(0, "False") is None
    assert out.options["movflags"] == "+nomobsync"
    assert "movflags" not in out.video_options


def test_probe_error_row_survives_save_load(qapp, tmp_path, probe):
    d = QueueDocument(CapabilityIndex.stub(),
                      prober=lambda p: (_ for _ in ()).throw(ValueError("probe failed")))
    d.add_files([str(tmp_path / "broken.mp4")])
    d.save(dir=tmp_path)
    loaded = QueueDocument.load(CapabilityIndex.stub(), dir=tmp_path, prober=probe)
    assert len(loaded.items) == 1
    assert loaded.items[0].error
    assert loaded.items[0].unparsed is not None


def test_healthy_row_round_trips_as_job(qapp, tmp_path, probe):
    d = QueueDocument(CapabilityIndex.stub(), prober=probe)
    d.add_files([str(tmp_path / "alpha.mp4")])
    d.save(dir=tmp_path)
    loaded = QueueDocument.load(CapabilityIndex.stub(), dir=tmp_path, prober=probe)
    assert loaded.items[0].error is None
    assert loaded.items[0].unparsed is None
    assert loaded.items[0].job.outputs[0].path == "alpha_out.mp4"
