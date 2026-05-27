# -*- coding: utf-8 -*-
"""
Point d'entrée du plugin QGIS « Outil Découpe Tronçon ».
Chargé automatiquement par le gestionnaire de plugins QGIS via la fonction classFactory.
"""


def classFactory(iface):
    from .plugin import PluginDecoupe
    return PluginDecoupe(iface)
