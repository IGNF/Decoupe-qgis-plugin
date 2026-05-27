# -*- coding: utf-8 -*-

from __future__ import annotations

import math
from typing import List

from qgis.PyQt.QtGui import QColor, QCursor
from ..qt_compat import Qt

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import (
    QgsMapCanvas,
    QgsMapMouseEvent,
    QgsMapTool,
    QgsRubberBand,
    QgsVertexMarker,
)

from ..traitement.moteur_decoupe_polygone import decouper_polygone, ErreurDecoupePolygone
from ..traitement.parametres import lire_champs_uniques



# Constantes

_SEARCH_PIXEL_RADIUS = 25      # pixels — rayon du cercle de sélection d'objet
_MIN_AREA_M2         = 1.0     # m² — surface minimale par polygone résultant

_COLOR_HIGHLIGHT = QColor(255, 165,  0, 180)   # orange semi-transparent — polygone sélectionné
_COLOR_CUT_LINE  = QColor(220,  0,   0, 220)   # rouge — ligne de coupe tracée
_COLOR_VERTEX    = QColor(220,  0,   0)        # rouge — marqueur de sommet



# OutilDecoupePolygone

class OutilDecoupePolygone(QgsMapTool):
    """
    Outil de découpe de polygone interactif en deux phases.

    Machine à états
    ---------------
    INACTIF  → clic gauche sur un polygone      → TRACÉ (1er sommet posé)
    TRACÉ    → clic gauche supplémentaire       → ajout de sommet
    TRACÉ    → déplacement de la souris         → prévisualisation du prochain segment
    TRACÉ    → clic droit (≥ 2 pts)            → exécution → INACTIF
    TRACÉ    → Echap / changement d'outil       → annulation → INACTIF
    """

    def __init__(self, canvas: QgsMapCanvas, iface):
        super().__init__(canvas)
        self._iface   = iface
        self._canvas  = canvas

        # État de tracé 
        self._pending_feature: QgsFeature | None    = None
        self._pending_layer:   QgsVectorLayer | None = None
        self._draw_pts:        List[QgsPointXY]      = []   # sommets confirmés (SCR carte)

        # Retour visuel 
        self._rb_polygon:  QgsRubberBand   | None = None   # surbrillance du polygone sélectionné
        self._rb_cutline:  QgsRubberBand   | None = None   # ligne de coupe tracée
        self._vtx_markers: List[QgsVertexMarker]  = []     # marqueurs de sommets

        self.setCursor(QCursor(Qt.CrossCursor))



    def canvasMoveEvent(self, event: QgsMapMouseEvent) -> None:
        """Met à jour le segment de prévisualisation (dernier sommet → curseur)."""
        if not self._draw_pts or self._rb_cutline is None:
            return
        # Supprime l'éventuel point de prévisualisation précédent
        if self._rb_cutline.numberOfVertices() > len(self._draw_pts):
            self._rb_cutline.removeLastPoint()
        self._rb_cutline.addPoint(self._ecran_vers_carte(event.pos()))

    def canvasPressEvent(self, event: QgsMapMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._clic_gauche(event)
        elif event.button() == Qt.RightButton:
            self._clic_droit(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._annuler()

    def deactivate(self) -> None:
        self._annuler()
        super().deactivate()


    def _clic_gauche(self, event: QgsMapMouseEvent) -> None:
        map_pt = self._ecran_vers_carte(event.pos())

        if self._pending_feature is None:
            # Phase 1 : sélection du polygone
            self._select_polygon(map_pt)
        else:
            # Phase 2 : ajout d'un sommet à la ligne de coupe
            self._add_vertex(map_pt)

    def _select_polygon(self, map_pt: QgsPointXY) -> None:
        """Sélectionne le polygone le plus proche du clic dans la couche active."""
        layer = self._iface.activeLayer()

        if not isinstance(layer, QgsVectorLayer):
            self._msg("Sélectionnez une couche vecteur polygone dans le panneau des couches.", "warning")
            return

        if QgsWkbTypes.geometryType(layer.wkbType()) != QgsWkbTypes.PolygonGeometry:
            self._msg("La couche active n'est pas une couche de polygones.", "warning")
            return

        if not layer.isEditable():
            self._msg(
                "La couche n'est pas en mode édition. "
                "Activez l'édition (crayon) avant de découper.",
                "warning",
            )
            return

        candidates = self._objets_au_point(map_pt, layer)
        if not candidates:
            self._msg(
                f"Aucun polygone trouvé à cet endroit (couche : {layer.name()}).",
                "info",
            )
            return

        layer_pt  = self._vers_scr_couche(map_pt, layer)
        feature   = self._objet_le_plus_proche(candidates, layer_pt)

        self._pending_feature = feature
        self._pending_layer   = layer

        # Premier sommet de la ligne = point du clic
        self._draw_pts.append(map_pt)
        self._draw_highlight(layer, feature)
        self._init_cutline()
        self._place_vertex_marker(map_pt)

        self._msg(
            f"Polygone sélectionné sur « {layer.name()} » — "
            "cliquez pour ajouter des sommets, clic droit pour confirmer la découpe.",
            "info",
        )

    def _add_vertex(self, map_pt: QgsPointXY) -> None:
        """Ajoute un sommet confirmé à la ligne de coupe."""
        # Supprime le point de prévisualisation dynamique avant d'ancrer le sommet
        if self._rb_cutline is not None and self._rb_cutline.numberOfVertices() > len(self._draw_pts):
            self._rb_cutline.removeLastPoint()

        self._draw_pts.append(map_pt)
        if self._rb_cutline is not None:
            self._rb_cutline.addPoint(map_pt)
        self._place_vertex_marker(map_pt)

        self._msg(
            f"{len(self._draw_pts)} sommet(s) — clic droit pour confirmer la découpe.",
            "info",
        )

    # Clic droit — confirmation et exécution de la découpe
    def _clic_droit(self, event: QgsMapMouseEvent) -> None:
        if self._pending_feature is None:
            self._msg("Faites d'abord un clic gauche pour sélectionner un polygone.", "warning")
            return

        if len(self._draw_pts) < 2:
            self._msg(
                "Tracez au moins 2 sommets avant de confirmer (clic droit).",
                "warning",
            )
            return

        layer   = self._pending_layer
        feature = self._pending_feature

        # Conversion des sommets en SCR couche
        split_pts = [self._vers_scr_couche(p, layer) for p in self._draw_pts]

        layer.beginEditCommand("Découpe polygone")
        try:
            result = decouper_polygone(
                layer,
                feature,
                split_pts,
                _MIN_AREA_M2,
                lire_champs_uniques(),
            )
        except ErreurDecoupePolygone as exc:
            layer.destroyEditCommand()
            self._annuler()
            self._msg(str(exc), "critical")
            return

        layer.endEditCommand()
        self._canvas.refresh()
        self._annuler()

        nb_parts = 1 + len(result.new_fids)
        self._msg(
            f"✓ Polygone découpé en {nb_parts} partie(s) — "
            f"surface principale : {result.area_largest:.1f} m².",
            "success",
        )

    def _draw_highlight(self, layer: QgsVectorLayer, feature: QgsFeature) -> None:
        self._clear_rb_polygon()
        rb = QgsRubberBand(self._canvas, QgsWkbTypes.PolygonGeometry)
        rb.setColor(_COLOR_HIGHLIGHT)
        rb.setWidth(3)
        rb.setToGeometry(feature.geometry(), layer)
        self._rb_polygon = rb

    def _init_cutline(self) -> None:
        self._clear_rb_cutline()
        rb = QgsRubberBand(self._canvas, QgsWkbTypes.LineGeometry)
        rb.setColor(_COLOR_CUT_LINE)
        rb.setWidth(2)
        if self._draw_pts:
            rb.addPoint(self._draw_pts[0])
        self._rb_cutline = rb

    def _place_vertex_marker(self, pt: QgsPointXY) -> None:
        m = QgsVertexMarker(self._canvas)
        m.setCenter(pt)
        m.setColor(_COLOR_VERTEX)
        m.setIconType(QgsVertexMarker.ICON_CIRCLE)
        m.setIconSize(8)
        m.setPenWidth(2)
        self._vtx_markers.append(m)

    # Annulation / réinitialisation
    def _annuler(self) -> None:
        self._clear_rb_polygon()
        self._clear_rb_cutline()
        self._clear_markers()
        self._pending_feature = None
        self._pending_layer   = None
        self._draw_pts        = []

    def _clear_rb_polygon(self) -> None:
        if self._rb_polygon is not None:
            self._rb_polygon.reset(QgsWkbTypes.PolygonGeometry)
            self._canvas.scene().removeItem(self._rb_polygon)
            self._rb_polygon = None

    def _clear_rb_cutline(self) -> None:
        if self._rb_cutline is not None:
            self._rb_cutline.reset(QgsWkbTypes.LineGeometry)
            self._canvas.scene().removeItem(self._rb_cutline)
            self._rb_cutline = None

    def _clear_markers(self) -> None:
        for m in self._vtx_markers:
            self._canvas.scene().removeItem(m)
        self._vtx_markers = []


    # Helpers de coordonnées / géométrie
    def _ecran_vers_carte(self, pos) -> QgsPointXY:
        return self.toMapCoordinates(pos)

    def _vers_scr_couche(self, pt: QgsPointXY, layer: QgsVectorLayer) -> QgsPointXY:
        map_crs = self._canvas.mapSettings().destinationCrs()
        if layer.crs() == map_crs:
            return pt
        xform = QgsCoordinateTransform(map_crs, layer.crs(), QgsProject.instance())
        return xform.transform(pt)

    def _objets_au_point(self, map_pt: QgsPointXY, layer: QgsVectorLayer) -> list:
        r = _SEARCH_PIXEL_RADIUS * self._canvas.mapUnitsPerPixel()
        map_rect = QgsRectangle(
            map_pt.x() - r, map_pt.y() - r,
            map_pt.x() + r, map_pt.y() + r,
        )
        map_crs = self._canvas.mapSettings().destinationCrs()
        if layer.crs() != map_crs:
            xform = QgsCoordinateTransform(map_crs, layer.crs(), QgsProject.instance())
            layer_rect = xform.transformBoundingBox(map_rect)
        else:
            layer_rect = map_rect

        request = QgsFeatureRequest().setFilterRect(layer_rect)
        results = []
        for feat in layer.getFeatures(request):
            geom = feat.geometry()
            if geom and not geom.isEmpty():
                results.append(QgsFeature(feat))
        return results

    @staticmethod
    def _objet_le_plus_proche(features: list, layer_pt: QgsPointXY) -> QgsFeature:
        pt_geom = QgsGeometry.fromPointXY(layer_pt)
        return min(features, key=lambda f: f.geometry().distance(pt_geom))

    # Barre de messages
    def _msg(self, text: str, level: str = "info") -> None:
        bar = self._iface.messageBar()
        dispatch = {
            "info":     bar.pushInfo,
            "warning":  bar.pushWarning,
            "success":  bar.pushSuccess,
            "critical": bar.pushCritical,
        }
        dispatch.get(level, bar.pushInfo)("Découpe Polygone", text)
