"""Gate: embedded %VAR% refs in bat tokens splice raw (expand at run time);
literal percent signs stay doubled."""
from ffgui.export.bat import bat_token


def test_embedded_env_ref_splices_raw():
    assert bat_token(r"%USERPROFILE%\Videos\o.mp4") == r'"%USERPROFILE%\Videos\o.mp4"'


def test_literal_percent_stays_doubled():
    assert bat_token("50%done.mp4") == '"50%%done.mp4"'
    assert bat_token("clip_100%.mp4") == '"clip_100%%.mp4"'


def test_full_env_ref_token_unchanged():
    assert bat_token("%TEMP%") == "%TEMP%"
