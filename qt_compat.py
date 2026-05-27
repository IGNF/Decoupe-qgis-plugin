# -*- coding: utf-8 -*-
"""
Mapping permettant la compatibilité de l'usage de constantes Qt entre les versions 5 et 6.
"""

from __future__ import annotations

from qgis.PyQt import QtCore
from qgis.PyQt.QtCore import Qt as _Qt
from qgis.PyQt.QtWidgets import QDialogButtonBox as _QDialogButtonBox

_QT_MAJOR: int = int(QtCore.qVersion().split(".")[0])

# ---------------------------------------------------------------------------
# Mapping Qt6 scoped-enum → alias name
# (vide sous Qt5 : l'accès plat fonctionne nativement)
# ---------------------------------------------------------------------------
_QT_ALIASES: dict[str, object] = {}
_QDB_ALIASES: dict[str, object] = {}

if _QT_MAJOR >= 6:
    _QT_ALIASES = {
        # Orientation
        "Horizontal":           _Qt.Orientation.Horizontal,
        "Vertical":             _Qt.Orientation.Vertical,
        # CursorShape
        "CrossCursor":          _Qt.CursorShape.CrossCursor,
        "ArrowCursor":          _Qt.CursorShape.ArrowCursor,
        "BlankCursor":          _Qt.CursorShape.BlankCursor,
        # MouseButton
        "LeftButton":           _Qt.MouseButton.LeftButton,
        "RightButton":          _Qt.MouseButton.RightButton,
        "MiddleButton":         _Qt.MouseButton.MiddleButton,
        "NoButton":             _Qt.MouseButton.NoButton,
        # Key
        "Key_Escape":           _Qt.Key.Key_Escape,
        "Key_Return":           _Qt.Key.Key_Return,
        "Key_Enter":            _Qt.Key.Key_Enter,
        "Key_Delete":           _Qt.Key.Key_Delete,
        "Key_Backspace":        _Qt.Key.Key_Backspace,
        # ItemFlag
        "ItemIsEnabled":        _Qt.ItemFlag.ItemIsEnabled,
        "ItemIsUserCheckable":  _Qt.ItemFlag.ItemIsUserCheckable,
        "ItemIsSelectable":     _Qt.ItemFlag.ItemIsSelectable,
        "ItemIsEditable":       _Qt.ItemFlag.ItemIsEditable,
        # CheckState
        "Checked":              _Qt.CheckState.Checked,
        "Unchecked":            _Qt.CheckState.Unchecked,
        "PartiallyChecked":     _Qt.CheckState.PartiallyChecked,
    }
    _QDB_ALIASES = {
        "Ok":       _QDialogButtonBox.StandardButton.Ok,
        "Cancel":   _QDialogButtonBox.StandardButton.Cancel,
        "Close":    _QDialogButtonBox.StandardButton.Close,
        "Save":     _QDialogButtonBox.StandardButton.Save,
        "Reset":    _QDialogButtonBox.StandardButton.Reset,
        "NoButton": _QDialogButtonBox.StandardButton.NoButton,
    }


class _Proxy:
    """
    Proxy transparent vers un objet Qt qui résout en premier les alias de
    compatibilité, puis délègue au vrai objet sous-jacent.

    Cela permet d'utiliser la syntaxe Qt5 (``Qt.Horizontal``) quel que soit
    la version de Qt installée.
    """

    __slots__ = ("_wrapped", "_aliases")

    def __init__(self, wrapped: object, aliases: dict) -> None:
        object.__setattr__(self, "_wrapped", wrapped)
        object.__setattr__(self, "_aliases", aliases)

    def __getattr__(self, name: str) -> object:
        aliases = object.__getattribute__(self, "_aliases")
        if name in aliases:
            return aliases[name]
        return getattr(object.__getattribute__(self, "_wrapped"), name)

    def __call__(self, *args: object, **kwargs: object) -> object:
        """Permet d'utiliser QDialogButtonBox(...) comme constructeur."""
        return object.__getattribute__(self, "_wrapped")(*args, **kwargs)


# ---------------------------------------------------------------------------
# Exports publics
# ---------------------------------------------------------------------------
Qt               = _Proxy(_Qt,               _QT_ALIASES)
QDialogButtonBox = _Proxy(_QDialogButtonBox, _QDB_ALIASES)
