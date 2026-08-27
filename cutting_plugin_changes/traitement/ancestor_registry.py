# -*- coding: utf-8 -*-
"""
Registre partagé des relations ancêtre-enfant créées par les outils de découpe.

Ce module stocke, dans l'espace de noms de l'interpréteur Python (``sys``),
un dictionnaire { (layer_id, new_fid): ancestor_cleabs } utilisé pour
communiquer au plugin Espace Collaboratif l'identifiant de l'objet d'origine
lors d'une transaction POST de type INSERT (découpe).

Le dictionnaire est accessible depuis n'importe quel plugin QGIS chargé dans
le même processus Python via :
    getattr(sys, '_ign_cutting_ancestors', {})
"""
from __future__ import annotations

import sys

_REGISTRY_KEY = '_ign_cutting_ancestors'


def register(layer_id: str, new_fid: int, original_fid: int) -> None:
    """
    Enregistre la relation ancêtre pour un nouvel objet découpé.

    :param layer_id:    Identifiant QGIS de la couche (QgsVectorLayer.id()).
    :param new_fid:     FID temporaire (négatif) attribué par QGIS au nouvel objet.
    :param original_fid: FID de l'objet d'origine dans la couche (positif).
                         Le plugin Espace Collaboratif l'utilisera pour retrouver
                         le cleabs via SQLiteManager.
    """
    if not hasattr(sys, _REGISTRY_KEY):
        setattr(sys, _REGISTRY_KEY, {})
    getattr(sys, _REGISTRY_KEY)[(layer_id, new_fid)] = original_fid
    print("[ancestor] register → clé=({!r}, {}), original_fid={}".format(layer_id, new_fid, original_fid))


def pop(layer_id: str, new_fid: int) -> int | None:
    """
    Récupère et supprime la relation ancêtre pour un objet.
    (Conservé pour compatibilité — préférer get() côté Espace Collaboratif.)

    :param layer_id: Identifiant QGIS de la couche.
    :param new_fid:  FID temporaire du nouvel objet.
    :return: Le FID de l'objet d'origine, ou None si aucune entrée n'est trouvée.
    """
    registry = getattr(sys, _REGISTRY_KEY, {})
    return registry.pop((layer_id, new_fid), None)


def get(layer_id: str, new_fid: int) -> int | None:
    """
    Retourne la relation ancêtre SANS la supprimer (lecture non-destructive).

    Utilisé par le plugin Espace Collaboratif pour survivre aux retries :
    si la transaction échoue et est retentée, l'entrée est toujours présente.

    :param layer_id: Identifiant QGIS de la couche.
    :param new_fid:  FID temporaire du nouvel objet.
    :return: Le FID de l'objet d'origine, ou None si aucune entrée n'est trouvée.
    """
    registry = getattr(sys, _REGISTRY_KEY, {})
    return registry.get((layer_id, new_fid), None)
