# -*- coding: utf-8 -*-

from __future__ import annotations

import math

from qgis.PyQt.QtGui import QColor, QCursor
from qgis.PyQt.QtWidgets import QInputDialog, QApplication
from ..qt_compat import Qt

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPoint,
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

from ..traitement.geometrie import trouver_point_coupe
from ..traitement.moteur_decoupe import decouper_troncon, ErreurDecoupe
from ..traitement.parametres import lire_champs_uniques



# Constantes
_SNAP_PIXEL_TOLERANCE = 15    # pixels — rayon d'accrochage sur sommet
_SEARCH_PIXEL_RADIUS  = 25    # pixels — rayon du cercle de sélection d'objet
_MIN_LENGTH_M         = 2.0   # mètres — longueur minimale par tronçon résultant

_COLOR_HIGHLIGHT  = QColor(255, 165,   0, 180)   # orange semi-transparent (surbrillance)
_COLOR_MARKER     = QColor(255,   0,   0)         # rouge (marqueur de coupe normal)
_COLOR_MARKER_VTX = QColor(255, 165,   0)         # orange (accrochage sur sommet existant)


class OutilDecoupeLigne(QgsMapTool):
    """
    Outil de découpe interactif en deux étapes pour les objets vectoriels linéaires.

    Machine à états
    ----------------
    INACTIF    → clic gauche sur un objet linéaire     → EN_ATTENTE
    EN_ATTENTE → déplacement de la souris             → mise à jour du marqueur
    EN_ATTENTE → clic droit                            → exécution → INACTIF
    EN_ATTENTE → Echap / changement d'outil            → annulation  → INACTIF
    """

    def __init__(self, canvas: QgsMapCanvas, iface):
        super().__init__(canvas)
        self._iface = iface
        self._canvas = canvas

        # --- État en attente ---
        self._pending_feature: QgsFeature | None = None
        self._pending_layer:   QgsVectorLayer | None = None
        self._pending_cut_pt:  QgsPoint | None = None
        self._pending_seg_idx: int = -1
        self._pending_is_vtx:  bool = False

        # --- Retour visuel ---
        self._rubber_band:  QgsRubberBand  | None = None
        self._cut_marker:   QgsVertexMarker | None = None

        self.setCursor(QCursor(Qt.CrossCursor))


    def canvasMoveEvent(self, event: QgsMapMouseEvent) -> None:
        """Met à jour le marqueur du point de coupe lorsque le curseur se déplace sur l'objet sélectionné."""
        if self._pending_feature is None or self._pending_layer is None:
            return

        map_pt   = self._ecran_vers_carte(event.pos())
        layer_pt = self._vers_scr_couche(map_pt, self._pending_layer)
        tol      = self._tolerance_accrochage(self._pending_layer)
        result   = trouver_point_coupe(
            layer_pt, self._pending_feature.geometry(), tol
        )
        if result is not None:
            cut_pt, seg_idx, is_vtx = result
            self._pending_cut_pt  = cut_pt
            self._pending_seg_idx = seg_idx
            self._pending_is_vtx  = is_vtx
            # Marker must be in map CRS
            marker_pt = self._vers_scr_carte(QgsPointXY(cut_pt.x(), cut_pt.y()), self._pending_layer)
            self._afficher_marqueur(marker_pt, is_vtx)

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


    # Clic gauche — sélection de l'objet et prévisualisation du point de coupe
    def _clic_gauche(self, event: QgsMapMouseEvent) -> None:
        self._annuler()   # efface tout état en attente précédent

        layer = self._iface.activeLayer()

        # la couche active doit être une couche linéaire éditable 
        if not isinstance(layer, QgsVectorLayer):
            self._msg(
                "Sélectionnez une couche vecteur linéaire dans le panneau des couches.",
                "warning",
            )
            return

        if QgsWkbTypes.geometryType(layer.wkbType()) != QgsWkbTypes.LineGeometry:
            self._msg(
                "La couche active n'est pas une couche de lignes.",
                "warning",
            )
            return

        if not layer.isEditable():
            self._msg(
                "La couche n'est pas en mode édition. "
                "Activez l'édition (crayon) avant de couper.",
                "warning",
            )
            return

        # Recherche des objets candidats sous le curseur 
        map_pt     = self._ecran_vers_carte(event.pos())
        layer_pt   = self._vers_scr_couche(map_pt, layer)
        candidates = self._objets_au_point(map_pt, layer)

        if not candidates:
            self._msg(
                f"Aucun objet linéaire trouvé à cet endroit "
                f"(couche : {layer.name()}, rayon : {self._rayon_recherche():.1f} u.).",
                "info",
            )
            return

        # Choix de l'objet géométriquement le plus proche (comparaison dans le SCR de la couche)
        feature = self._objet_le_plus_proche(candidates, layer_pt)

        # Calcul initial du point de coupe (en SCR couche) 
        tol = self._tolerance_accrochage(layer)
        result = trouver_point_coupe(layer_pt, feature.geometry(), tol)

        if result is None:
            self._msg(
                "Impossible de localiser un point de coupe sur cet objet. "
                "Essayez de cliquer plus près du centre du tronçon.",
                "warning",
            )
            return

        cut_pt, seg_idx, is_vtx = result

        # Mémorisation de l'état en attente (cut_pt est en SCR couche) 
        self._pending_feature = feature
        self._pending_layer   = layer
        self._pending_cut_pt  = cut_pt
        self._pending_seg_idx = seg_idx
        self._pending_is_vtx  = is_vtx

        # Retour visuel (le marqueur doit être en SCR carte)
        self._draw_highlight(layer, feature)
        marker_pt = self._vers_scr_carte(QgsPointXY(cut_pt.x(), cut_pt.y()), layer)
        self._afficher_marqueur(marker_pt, is_vtx)

        self._msg(
            f"Point de coupe prêt sur « {layer.name()} »  —  "
            "clic droit pour confirmer la découpe.",
            "info",
        )

    # Clic droit — confirmation et exécution de la découpe
    def _clic_droit(self, event: QgsMapMouseEvent) -> None:
        if self._pending_feature is None or self._pending_layer is None:
            self._msg(
                "Faites d'abord un clic gauche pour choisir le point de coupe.",
                "warning",
            )
            return

        layer   = self._pending_layer
        feature = self._pending_feature
        cut_pt  = self._pending_cut_pt
        seg_idx = self._pending_seg_idx
        is_vtx  = self._pending_is_vtx

        # Désambiguaësation lorsque plusieurs objets sont proches
        map_pt     = self._ecran_vers_carte(event.pos())
        candidates = self._objets_au_point(map_pt, layer)  # rectangle transformé en interne

        if len(candidates) > 1 and not any(
            f.id() == feature.id() for f in candidates
        ):
            # Le clic droit a atterri sur un autre groupe — on demande à l'utilisateur
            chosen = self._choisir_parmi(candidates, layer)
            if chosen is None:
                return  # l'utilisateur a annulé

            # Recalcul du point de coupe sur l'objet nouvellement choisi (cut_pt en SCR couche)
            tol = self._tolerance_accrochage(layer)
            result = trouver_point_coupe(
                QgsPointXY(cut_pt.x(), cut_pt.y()), chosen.geometry(), tol
            )
            if result is None:
                self._msg("Impossible de localiser le point de coupe sur cet objet.", "warning")
                return
            feature, cut_pt, seg_idx, is_vtx = chosen, *result

        # Exécution de la découpe dans une commande d'édition annulable
        layer.beginEditCommand("Découpe tronçon")
        try:
            result = decouper_troncon(
                layer, feature, cut_pt, seg_idx, is_vtx,
                _MIN_LENGTH_M,
                lire_champs_uniques(),
            )
        except ErreurDecoupe as exc:
            layer.destroyEditCommand()
            self._annuler()
            self._msg(str(exc), "critical")
            return

        layer.endEditCommand()

        # Rafraîchissement du canvas
        self._canvas.refresh()
        self._annuler()  # effacement de l'état visuel

        self._msg(
            f"✓ Découpe effectuée — "
            f"tronçon 1 : {result.length_1:.1f} m, "
            f"tronçon 2 : {result.length_2:.1f} m.",
            "success",
        )

    # Utilitaires : annulation / réinitialisation de l'état
    def _annuler(self) -> None:
        """Efface tout l'état en attente et le retour visuel associé."""
        self._clear_rubber_band()
        self._clear_cut_marker()
        self._pending_feature = None
        self._pending_layer   = None
        self._pending_cut_pt  = None
        self._pending_seg_idx = -1
        self._pending_is_vtx  = False


    def _draw_highlight(self, layer: QgsVectorLayer, feature: QgsFeature) -> None:
        self._clear_rubber_band()
        rb = QgsRubberBand(self._canvas, QgsWkbTypes.LineGeometry)
        rb.setColor(_COLOR_HIGHLIGHT)
        rb.setWidth(4)
        rb.setToGeometry(feature.geometry(), layer)
        self._rubber_band = rb

    def _afficher_marqueur(self, point: QgsPointXY, is_vertex: bool) -> None:
        self._clear_cut_marker()
        marker = QgsVertexMarker(self._canvas)
        marker.setCenter(point)
        marker.setColor(_COLOR_MARKER)
        marker.setFillColor(_COLOR_MARKER_VTX if is_vertex else _COLOR_MARKER)
        marker.setIconType(QgsVertexMarker.ICON_CROSS)
        marker.setIconSize(12)
        marker.setPenWidth(2)
        self._cut_marker = marker

    def _clear_rubber_band(self) -> None:
        if self._rubber_band is not None:
            self._rubber_band.reset(QgsWkbTypes.LineGeometry)
            self._canvas.scene().removeItem(self._rubber_band)
            self._rubber_band = None

    def _clear_cut_marker(self) -> None:
        if self._cut_marker is not None:
            self._canvas.scene().removeItem(self._cut_marker)
            self._cut_marker = None


    def _ecran_vers_carte(self, pos) -> QgsPointXY:
        return self.toMapCoordinates(pos)

    def _tolerance_accrochage(self, layer: QgsVectorLayer) -> float:
        """
        Tolérance d'accrochage en unités SCR de *layer*.
        Convertit _SNAP_PIXEL_TOLERANCE pixels → unités carte → unités couche.
        """
        r_map = _SNAP_PIXEL_TOLERANCE * self._canvas.mapUnitsPerPixel()
        map_crs = self._canvas.mapSettings().destinationCrs()
        if layer.crs() == map_crs:
            return r_map
        # Mise à l'échelle d'une unité de distance du SCR carte vers le SCR couche au centre du canvas
        c = self._canvas.center()
        p1 = self._vers_scr_couche(c, layer)
        p2 = self._vers_scr_couche(QgsPointXY(c.x() + r_map, c.y()), layer)
        return math.hypot(p2.x() - p1.x(), p2.y() - p1.y())

    def _rayon_recherche(self) -> float:
        """Rayon de recherche en unités SCR carte (utilisé dans le message de diagnostic)."""
        return _SEARCH_PIXEL_RADIUS * self._canvas.mapUnitsPerPixel()

    def _vers_scr_couche(self, pt: QgsPointXY, layer: QgsVectorLayer) -> QgsPointXY:
        """Transforme *pt* du SCR carte vers le SCR de *layer*."""
        map_crs = self._canvas.mapSettings().destinationCrs()
        if layer.crs() == map_crs:
            return pt
        xform = QgsCoordinateTransform(map_crs, layer.crs(), QgsProject.instance())
        return xform.transform(pt)

    def _vers_scr_carte(self, pt: QgsPointXY, layer: QgsVectorLayer) -> QgsPointXY:
        """Transforme *pt* du SCR de *layer* vers le SCR carte."""
        map_crs = self._canvas.mapSettings().destinationCrs()
        if layer.crs() == map_crs:
            return pt
        xform = QgsCoordinateTransform(layer.crs(), map_crs, QgsProject.instance())
        return xform.transform(pt)

    def _objets_au_point(
        self,
        map_pt: QgsPointXY,
        layer: QgsVectorLayer,
    ) -> list[QgsFeature]:
        """
        Retourne tous les objets linéaires dans un rayon de _SEARCH_PIXEL_RADIUS autour de *map_pt*.

        Le rectangle de recherche est construit en SCR carte puis transformé en SCR couche
        afin que ``getFeatures`` fonctionne correctement quel que soit le SCR de la couche.
        """
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
    def _objet_le_plus_proche(
        features: list[QgsFeature],
        layer_pt: QgsPointXY,
    ) -> QgsFeature:
        """
        Retourne l'objet dont la géométrie est géométriquement la plus proche de *layer_pt*.
        *layer_pt* doit être dans le SCR de la couche (identique aux géométries des objets).
        """
        pt_geom = QgsGeometry.fromPointXY(layer_pt)
        return min(features, key=lambda f: f.geometry().distance(pt_geom))

    @staticmethod
    def _choisir_parmi(
        features: list[QgsFeature],
        layer: QgsVectorLayer,
    ) -> QgsFeature | None:
        """
        Affiche une boîte de dialogue liste pour que l'utilisateur choisisse
        un objet parmi plusieurs candidats.
        Retourne le QgsFeature choisi, ou None si l'utilisateur a annulé.
        """
        display_field = layer.displayField()

        def _label(f: QgsFeature) -> str:
            if display_field:
                val = f[display_field]
                if val is not None:
                    return f"{display_field} = {val}  (FID {f.id()})"
            # Repli : premier champ texte non vide
            for field in f.fields():
                val = f[field.name()]
                if val and str(val).strip():
                    return f"{field.name()} = {val}  (FID {f.id()})"
            return f"FID {f.id()}"

        items = [_label(f) for f in features]
        chosen_label, ok = QInputDialog.getItem(
            None,
            "Plusieurs objets détectés",
            "Plusieurs tronçons sont présents à cet endroit.\n"
            "Sélectionnez l'objet à découper :",
            items,
            0,
            False,
        )
        if not ok:
            return None
        return features[items.index(chosen_label)]


    # Barre de messages
    def _msg(self, text: str, level: str = "info") -> None:
        bar = self._iface.messageBar()
        dispatch = {
            "info":     bar.pushInfo,
            "warning":  bar.pushWarning,
            "success":  bar.pushSuccess,
            "critical": bar.pushCritical,
        }
        dispatch.get(level, bar.pushInfo)("Découpe Tronçon", text)
