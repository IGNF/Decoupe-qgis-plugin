# -*- coding: utf-8 -*-
"""
Classe principale du plugin — enregistre le bouton dans la barre d'outils
et gère le cycle de vie de l'outil de découpe de tronçon.
"""
from __future__ import annotations

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QToolBar
from qgis.core import QgsApplication


class PluginDecoupe:
    """
    Objet principal du plugin QGIS, instancié une seule fois par le gestionnaire de plugins.
    Gère la barre d'outils, l'action du menu vecteur et l'instance de l'outil carte.
    """

    PLUGIN_NAME = "Outil Découpe Tronçon"
    TOOLBAR_LABEL = "Découpe Tronçon"

    def __init__(self, iface):
        self._iface = iface
        self._canvas = iface.mapCanvas()

        self._toolbar: QToolBar | None = None
        self._action_cut: QAction | None = None
        self._action_cut_polygon: QAction | None = None
        self._action_settings: QAction | None = None
        self._maptool = None
        self._maptool_polygon = None


    def initGui(self) -> None:
        """Appelé par QGIS lors du chargement du plugin — construit les éléments d'interface."""
        self._toolbar = self._iface.addToolBar(self.TOOLBAR_LABEL)
        self._toolbar.setObjectName("OutilDecoupeToolbar")

        icon_path = os.path.join(
            os.path.dirname(__file__), "cutting_road.png"
        )
        if os.path.isfile(icon_path):
            icon = QIcon(icon_path)
        else:
            icon = QgsApplication.getThemeIcon("/mActionSplitFeatures.svg")

        self._action_cut = QAction(
            icon,
            "Découper un tronçon",
            self._iface.mainWindow(),
        )
        self._action_cut.setCheckable(True)
        self._action_cut.setToolTip(
            "<b>Découper un tronçon</b><br>"
            "① Clic gauche : sélectionner le point de coupe<br>"
            "② Clic droit  : confirmer la découpe"
        )
        self._action_cut.triggered.connect(self._basculer_outil)

        self._toolbar.addAction(self._action_cut)

        # --- Bouton découpe polygone ---
        self._action_cut_polygon = QAction(
            QgsApplication.getThemeIcon("/mActionSplitParts.svg"),
            "Découper un polygone",
            self._iface.mainWindow(),
        )
        self._action_cut_polygon.setCheckable(True)
        self._action_cut_polygon.setToolTip(
            "<b>Découper un polygone</b><br>"
            "① Clic gauche : sélectionner le polygone à découper<br>"
            "② Clics gauches : tracer la ligne de coupe<br>"
            "③ Clic droit : confirmer la découpe"
        )
        self._action_cut_polygon.triggered.connect(self._basculer_outil_polygone)

        self._toolbar.addAction(self._action_cut_polygon)

        self._action_settings = QAction(
            QgsApplication.getThemeIcon("/mActionOptions.svg"),
            "Paramètres de l'outil Découpe",
            self._iface.mainWindow(),
        )
        self._action_settings.setToolTip(
            "<b>Paramètres</b><br>"
            "Configurer les champs identifiants uniques."
        )
        self._action_settings.triggered.connect(self._ouvrir_parametres)
        self._toolbar.addAction(self._action_settings)

        self._iface.addPluginToVectorMenu(self.PLUGIN_NAME, self._action_cut)
        self._iface.addPluginToVectorMenu(self.PLUGIN_NAME, self._action_cut_polygon)
        self._iface.addPluginToVectorMenu(self.PLUGIN_NAME, self._action_settings)

    def unload(self) -> None:
        """Appelé par QGIS lors du déchargement du plugin — nettoie les actions, la barre d'outils et l'outil actif."""
        if self._maptool is not None:
            self._canvas.unsetMapTool(self._maptool)
            self._maptool = None

        if self._maptool_polygon is not None:
            self._canvas.unsetMapTool(self._maptool_polygon)
            self._maptool_polygon = None

        if self._action_cut is not None:
            self._iface.removePluginVectorMenu(self.PLUGIN_NAME, self._action_cut)
            self._action_cut = None

        if self._action_cut_polygon is not None:
            self._iface.removePluginVectorMenu(self.PLUGIN_NAME, self._action_cut_polygon)
            self._action_cut_polygon = None

        if self._action_settings is not None:
            self._iface.removePluginVectorMenu(self.PLUGIN_NAME, self._action_settings)
            self._action_settings = None

        if self._toolbar is not None:
            self._iface.mainWindow().removeToolBar(self._toolbar)
            self._toolbar.deleteLater()
            self._toolbar = None


    # Activation / désactivation de l'outil carte
    def _basculer_outil(self, checked: bool) -> None:
        if checked:
            self._activer_outil()
        else:
            self._desactiver_outil()

    def _basculer_outil_polygone(self, checked: bool) -> None:
        if checked:
            self._activer_outil_polygone()
        else:
            self._desactiver_outil_polygone()

    def _activer_outil(self) -> None:
        """Instancie l'outil de découpe ligne si nécessaire, puis l'active sur le canvas."""
        from .interface.outil_decoupe_carte import OutilDecoupeLigne

        if self._maptool is None:
            self._maptool = OutilDecoupeLigne(self._canvas, self._iface)
            self._maptool.deactivated.connect(self._outil_desactive)

        # Désactive l'outil polygone si actif
        if self._action_cut_polygon is not None:
            self._action_cut_polygon.setChecked(False)

        self._canvas.setMapTool(self._maptool)
        self._action_cut.setChecked(True)

    def _activer_outil_polygone(self) -> None:
        """Instancie l'outil de découpe polygone si nécessaire, puis l'active sur le canvas."""
        from .interface.outil_decoupe_polygone_carte import OutilDecoupePolygone

        if self._maptool_polygon is None:
            self._maptool_polygon = OutilDecoupePolygone(self._canvas, self._iface)
            self._maptool_polygon.deactivated.connect(self._outil_polygone_desactive)

        # Désactive l'outil ligne si actif
        if self._action_cut is not None:
            self._action_cut.setChecked(False)

        self._canvas.setMapTool(self._maptool_polygon)
        self._action_cut_polygon.setChecked(True)

    def _desactiver_outil(self) -> None:
        if self._maptool is not None:
            self._canvas.unsetMapTool(self._maptool)
        self._action_cut.setChecked(False)

    def _desactiver_outil_polygone(self) -> None:
        if self._maptool_polygon is not None:
            self._canvas.unsetMapTool(self._maptool_polygon)
        self._action_cut_polygon.setChecked(False)

    def _outil_desactive(self) -> None:
        """Slot déclenché lorsque QGIS bascule vers un autre outil carte — décoche le bouton."""
        if self._action_cut is not None:
            self._action_cut.setChecked(False)

    def _outil_polygone_desactive(self) -> None:
        """Slot déclenché lorsque QGIS bascule vers un autre outil carte — décoche le bouton."""
        if self._action_cut_polygon is not None:
            self._action_cut_polygon.setChecked(False)

    def _ouvrir_parametres(self) -> None:
        """Ouvre la boîte de dialogue de configuration des champs identifiants uniques."""
        from .interface.dialogue_parametres import FenetreParametres
        from qgis.core import QgsVectorLayer
        layer = self._iface.activeLayer()
        dlg = FenetreParametres(
            self._iface.mainWindow(),
            layer if isinstance(layer, QgsVectorLayer) else None,
        )
        dlg.exec_()
