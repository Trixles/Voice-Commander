"""
core/settings/style.py
======================
Catppuccin-flavoured palette applied to the SettingsDialog and inherited
by any dialog-spawned popups via `parent.window().styleSheet()`.

Lives in its own module so the dialog code reads end-to-end without
the embedded CSS wall, and so any other window in the app can apply
the same palette by importing `STYLESHEET` / `build_stylesheet`.

Comes in two builds that differ ONLY in the window background's alpha:
`build_stylesheet(translucent=True)` paints a semi-opaque Catppuccin-base tint
(for desktops whose compositor blurs behind the window, so the blur frosts
through) and `translucent=False` paints the same base fully solid (for desktops
with no blur). The dialog picks between them at construction via
`core.env.blur_compositing_available()`.
"""

_STYLESHEET_TEMPLATE = """
    QDialog {
        background-color: __DIALOG_BG__;
        color: #cdd6f4;
    }
    QWidget#frostPanel {
        /* The single full-window backing surface. Translucent build: the one
           layer that carries the frosted tint. Opaque build: the solid base.
           Everything structural above it is transparent so this shows through
           uniformly (see dialog.py _build_ui and build_stylesheet below). */
        background-color: __FROST_BG__;
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
        /* Open at BOTH ends: no top border (tabs merge in at the top, same as
           before) and no bottom border, so the pane's L/R borders flow
           uninterrupted into the button bar's L/R borders below. The bar
           closes the box at the bottom. Net effect: one continuous frame with
           no horizontal divider above the buttons -- mirroring the top. */
        border: 1px solid #45475a;
        border-top: none;
        border-bottom: none;
        background-color: __PANE_BG__;
    }
    QTabBar::tab {
        background-color: __TAB_BG__;
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
        background-color: __TAB_SELECTED_BG__;
        color: #cdd6f4;
    }
    QTabBar::tab:hover:!selected {
        background-color: __TAB_HOVER_BG__;
        color: #cdd6f4;
    }
    QScrollArea, QScrollArea > QWidget > QWidget {
        background-color: __SCROLL_BG__;
    }
    QScrollArea {
        border: none;
    }
    QWidget#btnBar {
        /* Continue the bordered box from the tab pane above (which now has
           L/R borders only -- open at both ends). No top border here, so the
           button row flows seamlessly out of the pane with no horizontal
           divider, mirroring how the tab row merges into the body at the top.
           L/R continue the frame; the bottom border closes the box. */
        border-left: 1px solid #45475a;
        border-right: 1px solid #45475a;
        border-bottom: 1px solid #45475a;
    }
    QFrame#monitorFrame {
        border: 1px solid #45475a;
        border-radius: 4px;
    }
    QFrame#cmdBody {
        border: 1px solid #45475a;
        border-top: none;
        border-radius: 0 0 4px 4px;
        background-color: __BODY_BG__;
        margin: 0 0 6px 0;
    }
    QFrame#aliasBody {
        border: 1px solid #45475a;
        border-top: none;
        border-radius: 0 0 4px 4px;
        background-color: __BODY_BG__;
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
    QSlider::groove:horizontal {
        height: 6px;
        background: #45475a;
        border-radius: 3px;
    }
    QSlider::sub-page:horizontal {
        background: #89b4fa;
        border-radius: 3px;
    }
    QSlider::handle:horizontal {
        background: #cdd6f4;
        width: 16px;
        margin: -5px 0;
        border-radius: 8px;
    }
    QSlider::handle:horizontal:hover {
        background: #b4befe;
    }
"""

# How the two builds differ. The KEY idea: in the translucent build a SINGLE
# surface -- `#frostPanel`, the full-window backing widget added in dialog.py --
# carries one frosted tint, and every structural surface above it is
# `transparent` so the frost shows through uniformly. That avoids the two bugs a
# naive "tint every surface" approach hit: (1) nested tinted surfaces (dialog +
# pane + scroll area) STACKED, compounding to near-opaque; (2) surfaces with no
# background of their own (the tab-bar corner gaps, the button bar) were alpha-0
# holes that showed raw blurred wallpaper, because the top-level QDialog's own
# stylesheet background does NOT reliably paint under WA_TranslucentBackground.
# The opaque build (no compositor blur) keeps the original layered dark palette.
#
# `_FROST` is the lever for "how frosted": lower alpha = more blurred desktop
# bleeds through; higher = more solid. It's the only translucent surface with a
# real fill; everything else is transparent and just shows it.
_BASE = "#1e1e2e"           # Catppuccin base (opaque panel / solid frost base)
_BODY = "#181825"           # darker accent (tab bar, row bodies) in opaque build
_FROST = "rgba(30, 30, 46, 0.8)"   # #1e1e2e at 80% alpha -- the translucent tint

# Per-surface background values, keyed by build. Translucent: only #frostPanel
# fills; all else transparent (single frost layer, no stacking, no holes).
_THEME = {
    True: {  # translucent
        "__FROST_BG__":        _FROST,
        "__DIALOG_BG__":       "transparent",
        "__PANE_BG__":         "transparent",
        "__SCROLL_BG__":       "transparent",
        # Inactive tabs stay SOLID (opaque _BODY) so the frosted, see-through
        # active tab clearly stands out as "where you are" -- font colour alone
        # wasn't enough of a cue. Only the selected tab is transparent, so it
        # reads as merging into the frosted body below it.
        "__TAB_BG__":          _BODY,
        "__TAB_SELECTED_BG__": "transparent",
        "__TAB_HOVER_BG__":    "#313244",  # opaque highlight, matches solid tabs
        "__BODY_BG__":         "transparent",
    },
    False: {  # opaque -- the original layered palette
        "__FROST_BG__":        _BASE,
        "__DIALOG_BG__":       _BASE,
        "__PANE_BG__":         _BASE,
        "__SCROLL_BG__":       _BASE,
        "__TAB_BG__":          _BODY,
        "__TAB_SELECTED_BG__": _BASE,
        "__TAB_HOVER_BG__":    "#313244",
        "__BODY_BG__":         _BODY,
    },
}


def build_stylesheet(translucent: bool = True) -> str:
    """Return the dialog stylesheet, translucent or opaque.

    Pass the result of `core.env.blur_compositing_available()` as `translucent`
    so the window only goes see-through when a blur will composite behind it.
    """
    sheet = _STYLESHEET_TEMPLATE
    for token, value in _THEME[bool(translucent)].items():
        sheet = sheet.replace(token, value)
    return sheet


# Back-compat: importers that just want the default (translucent) sheet.
STYLESHEET = build_stylesheet(True)
