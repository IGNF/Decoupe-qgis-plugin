# -*- coding: utf-8 -*-
"""

  Le polygone le plus grand hérite de l'objet d'origine (UPDATE + FID conservé).
  Tous les autres polygones résultants sont de nouveaux objets (INSERT).
  Les champs identifiants uniques sont vidés sur les nouveaux objets afin que
  le serveur leur attribue une nouvelle valeur à l'insertion.

"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import List

from qgis.PyQt.QtCore import QDateTime, QTime
from qgis.core import (
    QgsDataSourceUri,
    QgsDistanceArea,
    QgsFeature,
    QgsFieldConstraints,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorLayer,
)

# Clé primaire auto-assignée par SpatiaLite — ne doit jamais être copiée
_SQLITE_PK_RE = re.compile(r'^id_sqlite_', re.IGNORECASE)

# Suppression de la précision sub-seconde dans les chaînes datetime
_SUBSECOND_RE = re.compile(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})\.\d+')

# Surface minimale (en m²) pour chaque polygone résultant
DEFAULT_MIN_AREA_M2: float = 1.0


class ErreurDecoupePolygone(Exception):
    """Levée lorsqu'une découpe ne peut pas être effectuée. Le message est destiné à l'utilisateur."""
    pass


@dataclass
class ResultatDecoupePolygone:
    """Contient toutes les informations sur une opération de découpe de polygone réussie."""
    original_fid: int               # FID de l'objet modifié (le plus grand polygone)
    new_fids: list                  # FIDs attribués aux nouveaux objets créés
    feature_largest: QgsFeature     # Polygone le plus grand (UPDATE)
    features_new: list              # Nouveaux polygones (INSERT)
    area_largest: float             # Surface du polygone le plus grand (m²)
    areas_new: list                 # Surfaces des nouveaux polygones (m²)


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def decouper_polygone(
    layer: QgsVectorLayer,
    feature: QgsFeature,
    split_pts: List[QgsPointXY],
    min_area: float = DEFAULT_MIN_AREA_M2,
    unique_id_fields: "tuple | list" = (),
) -> ResultatDecoupePolygone:
    """
    Exécute la découpe de *feature* (polygone) dans *layer* selon la ligne
    définie par *split_pts*.

    L'appelant doit :
      - avoir la couche en mode édition
      - encadrer cet appel par ``layer.beginEditCommand`` / ``layer.endEditCommand``

    Paramètres
    ----------
    layer            : QgsVectorLayer polygone en mode édition
    feature          : objet polygone à couper
    split_pts        : liste ≥ 2 de QgsPointXY formant la ligne de coupe (SCR couche)
    min_area         : surface minimale acceptable pour chaque polygone résultant (m²)
    unique_id_fields : noms des champs uniques (ex. ``cleabs``).
                       Conservés sur le plus grand polygone, vidés sur les autres.

    Retourne
    --------
    ResultatDecoupePolygone en cas de succès. Lève ErreurDecoupePolygone pour tout échec récupérable.
    """
    if not layer.isEditable():
        raise ErreurDecoupePolygone("La couche n'est pas en mode édition.")

    if len(split_pts) < 2:
        raise ErreurDecoupePolygone("La ligne de découpe doit comporter au moins 2 points.")

    # Copie de travail de la géométrie — splitGeometry modifie l'objet en place
    geom = QgsGeometry(feature.geometry())

    result_code, new_geoms, _ = geom.splitGeometry(split_pts, False)

    if result_code == QgsGeometry.NothingHappened:
        raise ErreurDecoupePolygone(
            "La ligne ne traverse pas complètement le polygone. "
            "Elle doit entrer et sortir du polygone pour le découper en deux parties."
        )
    if result_code != QgsGeometry.Success:
        raise ErreurDecoupePolygone(
            f"Impossible de découper le polygone (code interne : {result_code}). "
            "Vérifiez la validité de la géométrie."
        )

    # Toutes les parties : la géométrie modifiée + les nouvelles
    all_parts: list[QgsGeometry] = [geom] + list(new_geoms)

    # Mesure des surfaces en mètres²
    da = QgsDistanceArea()
    da.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
    da.setEllipsoid(QgsProject.instance().ellipsoid() or "GRS80")

    areas = [da.measureArea(g) for g in all_parts]

    for a in areas:
        if a < min_area:
            raise ErreurDecoupePolygone(
                f"Découpe refusée : un polygone résultant ferait {a:.2f} m² "
                f"(minimum autorisé : {min_area} m²)."
            )

    # Le plus grand hérite du FID d'origine (UPDATE)
    largest_idx = areas.index(max(areas))

    feat_largest = QgsFeature(feature)      # copie le FID + tous les attributs d'origine
    feat_largest.setGeometry(all_parts[largest_idx])

    # Les autres sont de nouveaux objets (INSERT) avec attributs assainis
    features_new: list[QgsFeature] = []
    areas_new: list[float] = []

    for i, (g, a) in enumerate(zip(all_parts, areas)):
        if i == largest_idx:
            continue
        feat_new = QgsFeature(feature.fields())
        feat_new.setGeometry(g)
        _copy_attributes_sanitized(feat_new, feature)

        # Vidage des champs identifiants uniques
        for field_name in unique_id_fields:
            uf_idx = layer.fields().indexOf(field_name)
            if uf_idx < 0:
                continue
            not_null = _field_is_not_null(layer, field_name, uf_idx)
            feat_new.setAttribute(uf_idx, "" if not_null else None)

        features_new.append(feat_new)
        areas_new.append(a)

    # Application en couche
    if not layer.changeGeometry(feature.id(), all_parts[largest_idx]):
        raise ErreurDecoupePolygone(
            "Impossible de modifier la géométrie de l'objet existant. "
            "Vérifiez que la couche est éditable et que l'objet n'est pas protégé."
        )

    new_fids: list[int] = []
    for feat_new in features_new:
        if not layer.addFeature(feat_new):
            raise ErreurDecoupePolygone("Impossible de créer un nouvel objet dans la couche.")
        new_fids.append(feat_new.id())
        # Enregistrement de la relation ancêtre pour le plugin Espace Collaboratif.
        # On stocke le FID de l'objet d'origine (positif) — le plugin Espace Collaboratif
        # retrouvera le cleabs via SQLiteManager sans dépendre des attributs QGIS de la couche.
        from .ancestor_registry import register as _register_ancestor
        _register_ancestor(layer.id(), feat_new.id(), feature.id())

    return ResultatDecoupePolygone(
        original_fid=feature.id(),
        new_fids=new_fids,
        feature_largest=feat_largest,
        features_new=features_new,
        area_largest=areas[largest_idx],
        areas_new=areas_new,
    )


# ---------------------------------------------------------------------------
# Utilitaires internes d'attributs
# ---------------------------------------------------------------------------

def _copy_attributes_sanitized(dest: QgsFeature, src: QgsFeature) -> None:
    """
    Copie tous les attributs de *src* vers *dest* en appliquant deux passes de nettoyage :
    1. Les PK auto-assignées SpatiaLite (``id_sqlite_*``) sont mises à NULL.
    2. Les valeurs datetime sub-seconde sont tronquées à la seconde entière.
    """
    fields = src.fields()
    values = list(src.attributes())

    for i, f in enumerate(fields):
        val = values[i]

        if _SQLITE_PK_RE.match(f.name()):
            values[i] = None
            continue

        if isinstance(val, str):
            values[i] = _SUBSECOND_RE.sub(r'\1', val)
        elif isinstance(val, QDateTime) and val.isValid():
            t = val.time()
            values[i] = QDateTime(
                val.date(),
                QTime(t.hour(), t.minute(), t.second()),
                val.timeSpec(),
            )

    dest.setAttributes(values)


def _field_is_not_null(layer: QgsVectorLayer, field_name: str, uf_idx: int) -> bool:
    """
    Retourne True si *field_name* porte une contrainte NOT NULL à n'importe quel niveau.
    """
    pf_idx = layer.dataProvider().fields().indexOf(field_name)
    if pf_idx >= 0:
        if (layer.dataProvider().fields().field(pf_idx).constraints().constraints()
                & QgsFieldConstraints.ConstraintNotNull):
            return True

    if (layer.fields().field(uf_idx).constraints().constraints()
            & QgsFieldConstraints.ConstraintNotNull):
        return True

    try:
        uri = QgsDataSourceUri(layer.dataProvider().dataSourceUri())
        db_path = uri.database()
        table = uri.table()
        if db_path and table:
            conn = sqlite3.connect(db_path)
            try:
                for row in conn.execute(f"PRAGMA table_info([{table}])"):
                    if row[1] == field_name:
                        return bool(row[3])
            finally:
                conn.close()
    except Exception:
        pass

    return False
