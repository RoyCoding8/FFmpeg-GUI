"""Second-wave hostile inputs: type confusion at every public seam.

Contract: a mistyped argument is refused loudly (error string, ValueError,
or IndexError) and never leaks TypeError/AttributeError, stores a mistyped
value, or wipes data. Strings with hostile *content* are covered elsewhere;
here every slot gets ints, floats, bools, None, bytes, lists, dicts, and
Qt-signal bool variants ('True'/'False'/'2'/'').
"""

import pytest
from fftui.ffmpeg.capability_index import CapabilityIndex
from fftui.model import Input, Job, Output

from ffgui.doc import QueueDocument, QueueItem

NON_STR = [0, 1.5, True, None, b'x', ['x'], {'k': 1}]


def _item():
    job = Job(inputs=[Input(path='a.mp4')], outputs=[Output(path='o.mp4')])
    return QueueItem(job, {'name': 'a', 'enabled': True, 'notes': '',
                           'source_hash': ''})


@pytest.fixture()
def doc(qapp):
    d = QueueDocument(CapabilityIndex.stub(), prober=lambda p: Input(path=p))
    d.items.append(_item())
    return d


@pytest.mark.parametrize(('setter', 'args'), [
    ('set_codec', ('stream',)),  # stream slot hostile below
    ('set_option_scope', None),
    ('set_option_key', None),
    ('set_output_field_key', None),
    ('set_metadata_key', None),
    ('set_filters_stream', None),
    ('set_stream_meta_spec', None),
    ('set_input_arg_flag', None),
    ('set_global_key', None),
])
@pytest.mark.parametrize('key', [*NON_STR, ('k',)])
def test_key_slots_refuse_non_str(doc, setter, args, key):
    if setter == 'set_codec':
        err = doc.set_codec(0, key, 'libx264')
    elif setter == 'set_option_scope':
        err = doc.set_option(0, key, 'crf', '23')
    elif setter == 'set_option_key':
        err = doc.set_option(0, 'video', key, '23')
    elif setter == 'set_output_field_key':
        err = doc.set_output_field(0, key, 'x')
    elif setter == 'set_metadata_key':
        err = doc.set_metadata(0, key, 'x')
    elif setter == 'set_filters_stream':
        err = doc.set_filters(0, key, ['scale=1:1'])
    elif setter == 'set_stream_meta_spec':
        err = doc.set_stream_meta(0, key, 'language', 'eng')
    elif setter == 'set_input_arg_flag':
        err = doc.set_input_arg(0, key, '5')
    elif setter == 'set_global_key':
        err = doc.set_option(0, 'global', key, 'x')
    assert isinstance(err, str) and err, (setter, key)
    doc.argv(0)


@pytest.mark.parametrize('row', ['0', None, 0.0, True, b'0', [0], {'r': 0}])
def test_row_handles_reject_non_int(doc, row):
    with pytest.raises(IndexError):
        doc.item(row)
    with pytest.raises(IndexError):
        doc.set_option(row, 'video', 'crf', '23')
    with pytest.raises(IndexError):
        doc.set_codec(row, 'video', 'libx264')
    with pytest.raises(IndexError):
        doc.set_two_pass(row, True)
    with pytest.raises(IndexError):
        doc.duplicate_row(row)
    assert len(doc.items) == 1


@pytest.mark.parametrize('rows', [None, 5, '0', [True], [False], ['0'], b'0'])
def test_row_lists_forgive_garbage(doc, rows):
    doc.items.append(_item())  # two rows: True/False must not alias 1/0
    before = [it.meta['name'] for it in doc.items]
    doc.remove_rows(rows)
    doc.move_rows(rows, 0)
    assert [it.meta['name'] for it in doc.items] == before
    # bulk never raises on garbage; bytes iterate as ints, which is fine
    assert isinstance(doc.bulk_set_option(rows, 'video', 'crf', '23'), dict)
    assert [it.meta['name'] for it in doc.items] == before


def test_bool_row_never_aliases_row_one(doc):
    doc.items.append(_item())
    with pytest.raises(IndexError):
        doc.duplicate_row(True)
    with pytest.raises(IndexError):
        doc.item(False)
    assert len(doc.items) == 2


@pytest.mark.parametrize(('on', 'want'), [
    ('True', True), ('1', True), ('TRUE', True), ('True ', True),
    ('False', False), ('2', False), ('0', False), ('', False),
    ('none', False), ('None', False), ('FALSE', False),
    (True, True), (False, False), (1, True), (0, False), (None, False),
])
def test_set_two_pass_coerces_qt_variants(doc, on, want):
    doc.set_two_pass(0, on)
    assert doc.items[0].job.two_pass is want


def test_set_filters_accepts_list_and_tuple(doc):
    assert doc.set_filters(0, 'video_filters', ['scale=1:1']) is None
    assert doc.set_filters(0, 'video_filters', ('yadif',)) is None
    assert doc.items[0].job.video_filters.filters == ['yadif']
    # a bare string is one spec, never an iterable of chars
    assert doc.set_filters(0, 'video_filters', 'scale=1:1') is None
    assert doc.items[0].job.video_filters.filters == ['scale=1:1']
    assert doc.set_filters(0, 'video_filters', None) is None
    assert doc.items[0].job.video_filters.filters == []


@pytest.mark.parametrize('value', [*NON_STR, ('t',), {'k': 1}])
def test_export_tokens_refuse_non_str(value):
    import pytest as _p

    from ffgui.export.bat import bat_token
    from ffgui.export.model import reject_hostile
    from ffgui.export.sh import sh_token
    for fn in (bat_token, sh_token, reject_hostile):
        with _p.raises(ValueError):
            fn(value)


@pytest.mark.parametrize('field', ['generator', 'ffmpeg_version_hash',
                                   'created_utc', 'summary'])
@pytest.mark.parametrize('value', [0, None, b'x', ['x'], {'k': 1}])
def test_script_header_rejects_non_str_fields(field, value):
    from ffgui.export.model import ScriptHeader
    kw = {'generator': 'g', 'ffmpeg_version_hash': 'h',
          'created_utc': 't', 'summary': 's', field: value}
    with pytest.raises(ValueError):
        ScriptHeader(**kw)


def test_save_queue_rejects_mistyped_rows(tmp_path):
    from fftui.model import Job as _J

    from ffgui.store import load_queue, save_queue
    good = [(_J.from_dict({'inputs': [{'path': 'a.mp4'}],
                           'outputs': [{'path': 'o.mp4'}]}),
             {'name': 'a', 'enabled': True, 'notes': '', 'source_hash': ''})]
    save_queue(good, tmp_path)
    before = (tmp_path / 'queue.json').read_bytes()
    for bad in (None, 'x', {}, {'job': {}}, [('nope', {})], [(None, {})],
                [(7, {})], [(_J(), {}, 'extra')]):
        with pytest.raises(ValueError):
            save_queue(bad, tmp_path)
    assert (tmp_path / 'queue.json').read_bytes() == before
    assert len(load_queue(tmp_path)) == 1


@pytest.mark.parametrize('value', [123, 4.5, True, b'x', ['x'], {'x': 1}])
def test_cache_dir_rejects_non_path(value):
    from ffgui.paths import cache_dir
    with pytest.raises(ValueError):
        cache_dir(value)


@pytest.fixture()
def wired(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    from ffgui.controller import Controller
    from ffgui.ui.shell import Shell
    monkeypatch.setenv('FFGUI_CACHE_DIR', str(tmp_path / 'cache'))
    shell = Shell()
    doc = QueueDocument(CapabilityIndex.stub(), prober=lambda p: Input(path=p))
    doc.items.append(_item())
    ctl = Controller(shell, doc, CapabilityIndex.stub(),
                     QSettings(str(tmp_path / 's.ini'),
                               QSettings.Format.IniFormat))
    shell.queue.selectRow(0)
    return shell, doc, ctl


@pytest.mark.parametrize('kind', ['nope', '', 'CODEC', None, 123,
                                  ['codec'], {'k': 1}, b'codec'])
@pytest.mark.parametrize('value', ['x', 0, None, ['x']])
def test_on_tab_edit_unknown_kind_is_loud(wired, kind, value):
    shell, doc, ctl = wired
    messages = []
    ctl.status.connect(messages.append)
    ctl._on_tab_edit(kind, 'crf', value)  # must not raise
    assert messages  # the refusal is surfaced, not swallowed
    doc.argv(0)  # and must not store anything unbuildable


def test_apply_advanced_coerces_non_str_value_to_clear(wired):
    """Non-string values coerce to clear per the tree-wide wire contract
    (they can only arrive programmatically — Qt signals always send text);
    clearing is never an error, and nothing unbuildable is stored."""
    shell, doc, ctl = wired
    # streamtag excluded: with empty entry boxes it reports (covered by the
    # stream-spec tests), which is loud, not a crash — the point here.
    for kind in ('filters', 'chapters', 'burn', 'volume', 'loudnorm',
                 'faststart', 'hwdecode', 'hwdevice', 'segment',
                 'segment_time', 'bsf', 'tee', 'input'):
        key = 'video_filters' if kind == 'filters' else '-ss'
        for value in (0, None, ['x'], {'v': 1}):
            assert ctl._apply_advanced(kind, key, value, 0) is None, (kind, value)
    doc.argv(0)


def test_apply_advanced_validates_chapter_wire_shape(wired):
    shell, doc, ctl = wired
    assert isinstance(ctl._apply_advanced('chapters', '', 'x=y', 0), str)
    assert ctl._apply_advanced('chapters', '', '0\x1fIntro\x1feng', 0) is None


@pytest.mark.parametrize('value', [*NON_STR, 'True', 'False', ''])
def test_apply_flag_coerces_value_to_bool(wired, value):
    """Flag values coerce through the shared parser (non-strings read as
    off); the stored field is always a real bool, never the raw value."""
    shell, doc, ctl = wired
    assert ctl._apply_flag(0, 'two_pass', value) is None
    assert isinstance(doc.items[0].job.two_pass, bool)


def test_apply_flag_refuses_garbage_text(wired):
    """'2' is neither truthy nor falsy toggle text — no widget (toggled
    arrives as 'True'/'False'), preset, or internal caller produces it —
    so refusing beats silently reading it as on or off."""
    shell, doc, ctl = wired
    err = ctl._apply_flag(0, 'two_pass', '2')
    assert isinstance(err, str) and 'true' in err
    assert doc.items[0].job.two_pass is False


@pytest.mark.parametrize('key', [None, 123, ['k'], {'k': 1}, b'k'])
def test_apply_option_guards_key(wired, key):
    shell, doc, ctl = wired
    assert isinstance(ctl._apply_option(0, key, '23'), str)


def test_add_paths_forgives_shapes(wired, tmp_path):
    shell, doc, ctl = wired
    n = len(doc)
    ctl.add_paths(None)
    assert len(doc) == n
    p = tmp_path / 'solo.mp4'
    p.write_bytes(b'0')
    ctl.add_paths(str(p))
    assert len(doc) == n + 1
    ctl.add_paths(['ok.mp4', 5, None, ['x'], {'p': 1}])
    assert len(doc) == n + 2  # only the usable string was added


@pytest.mark.parametrize('path', [123, None, ['x'], {'p': 1}])
def test_export_to_rejects_non_path(wired, path):
    shell, doc, ctl = wired
    with pytest.raises(ValueError):
        ctl.export_to(path, 'sh')
