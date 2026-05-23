"""
core/settings/style.py
======================
Catppuccin-flavoured palette applied to the SettingsDialog and inherited
by any dialog-spawned popups via `parent.window().styleSheet()`.

Lives in its own module so the dialog code reads end-to-end without
the embedded CSS wall, and so any other window in the app can apply
the same palette by importing `STYLESHEET`.
"""

STYLESHEET = """
    QDialog {
        background-color: transparent;
        color: #cdd6f4;
    }
    QLabel {
        color: #cdd6f4;
    }
    QLineEdit, QPlainTextEdit, QComboBox {
        background-color: #313244;
        color: #cdd6f4;
        border: 1px solid #45475a;
        border-radius: 4px;
        padding: 3px 6px;
    }
    QLineEdit:focus, QPlainTextEdit:focus {
        border-color: #89b4fa;
    }
    QLineEdit:read-only, QPlainTextEdit:read-only {
        background-color: #282839;
        color: #6c7086;
    }
    QPushButton {
        background-color: #313244;
        color: #cdd6f4;
        border: 1px solid #45475a;
        border-radius: 4px;
        padding: 4px 10px;
    }
    QPushButton:hover {
        background-color: #45475a;
    }
    QPushButton:pressed {
        background-color: #585b70;
    }
    QPushButton:disabled {
        /* Distinctly inert so the user can see at a glance that this
           button can't be clicked right now. Background drops to the
           tab-bar dark tone; text and border drop to the dim overlay
           color (matches how :read-only QLineEdits are styled). */
        background-color: #181825;
        color: #45475a;
        border-color: #313244;
    }
    QPushButton#deleteBtn {
        background-color: #3a1f2d;
        color: #f38ba8;
        border-color: #f38ba8;
        font-weight: bold;
    }
    QPushButton#deleteBtn:hover {
        background-color: #f38ba8;
        color: #1e1e2e;
    }
    QPushButton#resetBtn {
        background-color: #3a1f2d;
        color: #f38ba8;
        border-color: #f38ba8;
    }
    QPushButton#resetBtn:hover {
        background-color: #f38ba8;
        color: #1e1e2e;
    }
    QPushButton#resetBtn:disabled {
        /* When the active tab has no defaults to restore, the red
           tinting would look weirdly inviting. Override back to the
           neutral disabled style. */
        background-color: #181825;
        color: #45475a;
        border-color: #313244;
    }
    QTabWidget::pane {
        border: 1px solid #45475a;
        border-top: none;
        background-color: transparent;
    }
    QTabBar::tab {
        background-color: #181825;
        color: #6c7086;
        border: 1px solid #45475a;
        border-bottom: none;
        border-right: none;
        border-radius: 4px 4px 0 0;
        padding: 6px 8px;
        margin-right: 0;
    }
    QTabBar::tab:!selected {
        border-right: 1px solid #45475a;
    }
    QTabBar::tab:last {
        border-right: 1px solid #45475a;
    }
    QTabBar::tab:selected {
        background-color: transparent;
        color: #cdd6f4;
    }
    QTabBar::tab:hover:!selected {
        background-color: #313244;
        color: #cdd6f4;
    }
    QScrollArea, QScrollArea > QWidget > QWidget {
        background-color: transparent;
    }
    QScrollArea {
        border: none;
    }
    QWidget#btnBar {
        /* Continue the bordered box from the tab pane above (which has L/R/B
           borders, no top). The pane's bottom border doubles as this bar's
           top divider, so the button row reads as part of the framed dialog
           rather than a detached strip. */
        border-left: 1px solid #45475a;
        border-right: 1px solid #45475a;
        border-bottom: 1px solid #45475a;
        border-top: 1px solid #45475a;
    }
    QFrame#monitorFrame {
        border: 1px solid #45475a;
        border-radius: 4px;
    }
    QFrame#cmdBody {
        border: 1px solid #45475a;
        border-top: none;
        border-radius: 0 0 4px 4px;
        background-color: #181825;
        margin: 0 0 6px 0;
    }
    QFrame#aliasBody {
        border: 1px solid #45475a;
        border-top: none;
        border-radius: 0 0 4px 4px;
        background-color: #181825;
        margin: 0 0 6px 0;
    }
    QScrollBar:vertical {
        background: #181825;
        width: 8px;
        border-radius: 4px;
    }
    QScrollBar::handle:vertical {
        background: #45475a;
        border-radius: 4px;
        min-height: 24px;
    }
    QScrollBar::handle:vertical:hover {
        background: #585b70;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0;
    }
    QDialogButtonBox QPushButton {
        min-width: 80px;
    }
    QComboBox QAbstractItemView {
        background-color: #313244;
        color: #cdd6f4;
        selection-background-color: #45475a;
    }
    QListView {
        background-color: #313244;
        color: #cdd6f4;
        border: 1px solid #45475a;
        border-radius: 4px;
    }
    QListView::item:selected {
        background-color: #45475a;
    }
    QListView::item:hover {
        background-color: #383850;
    }
"""
