# -*- coding: utf-8 -*-

from __future__ import annotations

import math
from typing import Optional, Tuple

from qgis.core import (
    QgsGeometry,
    QgsLineString,
    QgsMultiLineString,
    QgsAbstractGeometry,
    QgsPoint,
    QgsPointXY,
    QgsWkbTypes,
)



def trouver_point_coupe(
    map_pt: QgsPointXY,
    feature_geom: QgsGeometry,
    snap_tolerance: float,
) -> Optional[Tuple[QgsPoint, int, bool]]:
    """
    Paramètres
    ----------
    map_pt        : position du clic en coordonnées carte
    feature_geom  : QgsGeometry de l'objet candidat (doit être une ligne)
    snap_tolerance: distance en unités carte pour l'accrochage sur sommet

    Retourne
    --------
    (cut_point, segment_index, snapped_to_vertex)

    *segment_index* est l'index 0-basé du segment *contenant* la coupe :
      - si accrochage sur le sommet *i* : segment_index = i  (coupe entre seg i-1 et seg i)
      - si coupe au milieu d'un segment : segment_index = i  (coupe dans [sommet i, sommet i+1])

    Retourne None si aucun point valide n'est trouvé.
    
    Localise le meilleur point de coupe sur *feature_geom* le plus proche de *map_pt*.

    Stratégie (calquée sur l'outil GeoConcept d'origine) :
      1. Si un sommet existant se trouve dans *snap_tolerance* unités carte → accrochage
         sur ce sommet (sauf si c'est le premier ou le dernier sommet de la ligne).
      2. Sinon, projection de *map_pt* sur le segment le plus proche et recherche
         du point projeté le plus proche dans *snap_tolerance*.
    """
    if feature_geom is None or feature_geom.isEmpty():
        return None

    line = _extract_linestring(feature_geom)
    if line is None:
        return None

    n_pts = line.numPoints()
    if n_pts < 2:
        return None

    has_z = QgsWkbTypes.hasZ(feature_geom.wkbType())

    # Passe 1 : sommet le plus proche
    best_vertex_dist = float("inf")
    best_vertex_idx = -1

    for i in range(n_pts):
        v = line.pointN(i)
        d = math.hypot(v.x() - map_pt.x(), v.y() - map_pt.y())
        if d < best_vertex_dist:
            best_vertex_dist = d
            best_vertex_idx = i

    if best_vertex_dist <= snap_tolerance:
        # Ne pas couper au tout début ou à la toute fin — cela produirait un segment vide
        if best_vertex_idx == 0 or best_vertex_idx == n_pts - 1:
            return None
        vertex = line.pointN(best_vertex_idx)
        return vertex, best_vertex_idx, True

    # Passe 2 : projection sur le segment le plus proche
    best_proj_dist = float("inf")
    best_proj_pt: Optional[QgsPoint] = None
    best_proj_seg = -1

    for i in range(n_pts - 1):
        a = line.pointN(i)
        b = line.pointN(i + 1)
        proj_pt = _project_point_on_segment(map_pt, a, b, has_z)
        if proj_pt is None:
            continue
        d = math.hypot(proj_pt.x() - map_pt.x(), proj_pt.y() - map_pt.y())
        if d < best_proj_dist:
            best_proj_dist = d
            best_proj_pt = proj_pt
            best_proj_seg = i

    if best_proj_pt is None or best_proj_seg < 0:
        return None

    return best_proj_pt, best_proj_seg, False


def couper_ligne_au_point(
    feature_geom: QgsGeometry,
    cut_pt: QgsPoint,
    seg_idx: int,
    is_vertex: bool,
) -> Optional[Tuple[QgsGeometry, QgsGeometry]]:
    """
    Coupe *feature_geom* au point *cut_pt* et retourne (partie1, partie2).

    - partie1 : du début de la ligne d'origine → cut_pt
    - partie2 : de cut_pt → à la fin de la ligne d'origine

    Les deux parties conservent les valeurs Z. Retourne None en cas d'échec.

    Paramètres
    ----------
    feature_geom : géométrie linéaire d'origine
    cut_pt       : QgsPoint du point de coupe (avec Z si la géométrie est 3D)
    seg_idx      : index 0-basé du segment où se produit la coupe
    is_vertex    : True si cut_pt est un sommet existant à la position seg_idx
    """
    line = _extract_linestring(feature_geom)
    if line is None:
        return None

    n_pts = line.numPoints()

    pts1: list[QgsPoint] = []
    pts2: list[QgsPoint] = []

    if is_vertex:
        # cut_pt est déjà le sommet numéro seg_idx
        # partie1 : sommets 0 … seg_idx  (inclus)
        # partie2 : sommets seg_idx … n_pts-1  (inclus, sommet partagé)
        for i in range(seg_idx + 1):
            pts1.append(_clone_point(line.pointN(i)))
        for i in range(seg_idx, n_pts):
            pts2.append(_clone_point(line.pointN(i)))
    else:
        # cut_pt se trouve à l'intérieur du segment [seg_idx, seg_idx+1]
        # partie1 : sommets 0 … seg_idx, puis cut_pt
        # partie2 : cut_pt, puis sommets seg_idx+1 … n_pts-1
        for i in range(seg_idx + 1):
            pts1.append(_clone_point(line.pointN(i)))
        pts1.append(_clone_point(cut_pt))

        pts2.append(_clone_point(cut_pt))
        for i in range(seg_idx + 1, n_pts):
            pts2.append(_clone_point(line.pointN(i)))

    if len(pts1) < 2 or len(pts2) < 2:
        return None

    ls1 = QgsLineString()
    for p in pts1:
        ls1.addVertex(p)

    ls2 = QgsLineString()
    for p in pts2:
        ls2.addVertex(p)

    # QgsGeometry(clone()) — transfert de propriété sûre
    return QgsGeometry(ls1.clone()), QgsGeometry(ls2.clone())


# Fonctions internes
def _extract_linestring(geom: QgsGeometry) -> Optional[QgsLineString]:
    """
    Extrait un QgsLineString d'une géométrie simple ou multi-ligne.
    Pour les multi-lignes, retourne la première partie.
    """
    abs_geom: QgsAbstractGeometry = geom.constGet()

    if isinstance(abs_geom, QgsLineString):
        return abs_geom

    if isinstance(abs_geom, QgsMultiLineString):
        if abs_geom.numGeometries() > 0:
            part = abs_geom.geometryN(0)
            if isinstance(part, QgsLineString):
                return part

    # Repli : tentative de conversion en ligne simple
    converted = geom.convertToType(QgsWkbTypes.LineGeometry, destMultiType=False)
    if converted and not converted.isEmpty():
        inner = converted.constGet()
        if isinstance(inner, QgsLineString):
            return inner

    return None


def _project_point_on_segment(
    p: QgsPointXY,
    a: QgsPoint,
    b: QgsPoint,
    has_z: bool,
) -> Optional[QgsPoint]:
    """
    Projette *p* sur le segment [*a*, *b*].
    Retourne le QgsPoint projeté (avec Z interpolé si *has_z*), ou None
    si le segment est dégénéré (longueur quasi nulle).
    """
    dx = b.x() - a.x()
    dy = b.y() - a.y()
    seg_len_sq = dx * dx + dy * dy

    if seg_len_sq < 1e-14:
        return None

    # Position paramétrique sur le segment, bloquée dans [0, 1]
    t = ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))

    proj_x = a.x() + t * dx
    proj_y = a.y() + t * dy

    if has_z:
        za = a.z() if not math.isnan(a.z()) else 0.0
        zb = b.z() if not math.isnan(b.z()) else 0.0
        proj_z = za + t * (zb - za)
        return QgsPoint(proj_x, proj_y, proj_z)

    return QgsPoint(proj_x, proj_y)


def _clone_point(pt: QgsPoint) -> QgsPoint:
    """Retourne une copie indépendante de *pt* (gestion du Z incluse)."""
    if pt.is3D():
        return QgsPoint(pt.x(), pt.y(), pt.z())
    return QgsPoint(pt.x(), pt.y())
