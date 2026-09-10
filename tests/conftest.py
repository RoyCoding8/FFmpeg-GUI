import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def close_top_level_widgets() -> None:
    """Destroy every top-level widget still alive.

    Widget tests never delete what they create, so without this the
    windows of all earlier tests pile up in the session QApplication:
    tens of thousands of leaked widgets slow the whole suite and make
    app-wide setStyleSheet repolish effectively hang.
    """
    widgets = sys.modules.get("PySide6.QtWidgets")
    if widgets is None:
        return
    if widgets.QApplication.instance() is None:
        return
    for window in widgets.QApplication.topLevelWidgets():
        window.close()
        window.deleteLater()
    # DeferredDelete events are not flushed by processEvents() on every
    # platform; dispatch them explicitly so the widgets die now.
    qtcore = sys.modules.get("PySide6.QtCore")
    if qtcore is None:
        return
    qtcore.QCoreApplication.sendPostedEvents(None, qtcore.QEvent.Type.DeferredDelete)


@pytest.fixture(autouse=True)
def _close_top_level_widgets_after_test():
    yield
    close_top_level_widgets()
