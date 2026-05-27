# -*- coding: utf-8 -*-

from __future__ import annotations

from qgis.PyQt.QtWidgets import (
    QDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)
from ..qt_compat import Qt, QDialogButtonBox
from qgis.core import QgsVectorLayer

from ..traitement.parametres import lire_champs_uniques, sauver_champs_uniques


class FenetreParametres(QDialog):
    """
    Configurateur des champs identifiants uniques, basé sur les champs
    de la couche vectorielle active passée en paramètre.
    """

    def __init__(self, parent=None, layer: QgsVectorLayer | None = None):
        super().__init__(parent)
        self._layer = layer if isinstance(layer, QgsVectorLayer) else None
        self.setWindowTitle("Paramètres — Découpe Tronçon")
        self.setMinimumWidth(420)
        self._creer_interface()
        self._load()


    # Construction de l'interface
    def _creer_interface(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        lbl = QLabel(
            "<b>Champs identifiants uniques</b><br>"
            "Après une découpe, les champs cochés sont conservés sur le tronçon le plus "
            "long et effacés sur le nouveau tronçon afin que le serveur lui attribue "
            "une nouvelle valeur à l'insertion."
        )
        lbl.setWordWrap(True)
        root.addWidget(lbl)

        if self._layer is not None:
            layer_lbl = QLabel(f"<i>Couche active : {self._layer.name()}</i>")
            root.addWidget(layer_lbl)
        else:
            warn = QLabel(
                "<span style='color:darkorange;'>&#9888; Aucune couche vectorielle active — "
                "activez une couche linéaire puis rouvrez ce dialogue pour cocher les champs.</span>"
            )
            warn.setWordWrap(True)
            root.addWidget(warn)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        root.addWidget(self._list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal,
        )
        buttons.accepted.connect(self._valider)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

   
    # Chargement / Enregistrement des champs sélectionnés
    def _load(self) -> None:
        self._list.clear()
        current = set(lire_champs_uniques())

        if self._layer is None:
            # Pas de couche : affiche les champs enregistrés en lecture seule
            for name in sorted(current):
                item = QListWidgetItem(f"{name}  (enregistré)")
                item.setFlags(Qt.ItemIsEnabled)
                self._list.addItem(item)
            return

        fields = self._layer.fields()
        for i in range(fields.count()):
            name = fields.field(i).name()
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in current else Qt.Unchecked)
            self._list.addItem(item)

    def _valider(self) -> None:
        if self._layer is None:
            self.accept()
            return
        fields = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.Checked:
                fields.append(item.text())
        sauver_champs_uniques(fields)
        self.accept()
