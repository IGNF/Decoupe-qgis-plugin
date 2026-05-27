# -*- coding: utf-8 -*-

from __future__ import annotations

from qgis.core import QgsSettings

_KEY = "outil_decoupe/unique_id_fields"
_DEFAULT: list[str] = ["cleabs"]


def lire_champs_uniques() -> list[str]:
    """Retourne la liste courante des noms de champs identifiants uniques."""
    s = QgsSettings()
    raw = s.value(_KEY, None)
    if raw is None:
        return list(_DEFAULT)
    # QgsSettings peut retourner une liste Python ou une chaîne séparée par des virgules
    # selon la version de Qt/PyQt utilisée.
    if isinstance(raw, list):
        return [f.strip() for f in raw if f.strip()]
    return [f.strip() for f in str(raw).split(",") if f.strip()]


def sauver_champs_uniques(fields: list[str]) -> None:
    """Enregistre *fields* comme liste des champs identifiants uniques dans QgsSettings."""
    QgsSettings().setValue(_KEY, [f.strip() for f in fields if f.strip()])
