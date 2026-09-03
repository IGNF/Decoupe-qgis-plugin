# -*- coding: utf-8 -*-

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from qgis.PyQt.QtCore import QDateTime, QTime
from qgis.core import (
    QgsDataSourceUri,
    QgsDistanceArea,
    QgsFeature,
    QgsFieldConstraints,
    QgsGeometry,
    QgsPoint,
    QgsProject,
    QgsVectorLayer,
)

from .geometrie import couper_ligne_au_point

# Couche BDUni troncon_de_route — identifiée par son nom (sans casse)
_BDUNI_TRONCON_LAYER = "troncon_de_route"

# Champs de plages d'adresses BDUni — vidés sur le nouveau tronçon (le plus court).
# Le serveur BDUni recalcule les BP la nuit sur les deux objets via le mécanisme LOS/BAN.
_BP_FIELDS = frozenset({
    "borne_debut_droite",
    "borne_debut_gauche",
    "borne_fin_droite",
    "borne_fin_gauche",
    "bornes_debut_interpolees",
    "bornes_fin_interpolees",
})


def _is_bduni_troncon(layer_name: str) -> bool:
    return layer_name.strip().lower() == _BDUNI_TRONCON_LAYER

# Longueur minimale (mètres) pour chaque tronçon résultant
DEFAULT_MIN_LENGTH_M: float = 2.0

# Clé primaire auto-assignée par SpatiaLite — ne doit jamais être copiée vers les nouveaux objets
_SQLITE_PK_RE = re.compile(r'^id_sqlite_', re.IGNORECASE)

# Suppression de la précision sub-seconde dans les chaînes datetime — l'API BDUni
# n'accepte que les secondes entières ("2006-08-25 15:32:02"), pas les milli/microsecondes.
_SUBSECOND_RE = re.compile(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})\.\d+')



class ErreurDecoupe(Exception):
    """Levée lorsqu'une découpe ne peut pas être effectuée. Le message est destiné à l'utilisateur."""
    pass


@dataclass
class ResultatDecoupe:
    """Contient toutes les informations sur une opération de découpe réussie."""
    original_fid: int           # FID de l'objet modifié (premier tronçon)
    new_fid: int                # FID attribué au nouvel objet créé (deuxième tronçon)
    feature_1: QgsFeature       # Premier tronçon mis à jour  (début → point de coupe)
    feature_2: QgsFeature       # Deuxième tronçon nouvellement créé (point de coupe → fin)
    length_1: float             # Longueur 2D de la partie 1 (unités carte)
    length_2: float             # Longueur 2D de la partie 2 (unités carte)



# Point d'entrée principal

def decouper_troncon(
    layer: QgsVectorLayer,
    feature: QgsFeature,
    cut_pt: QgsPoint,
    seg_idx: int,
    is_vertex: bool,
    min_length: float = DEFAULT_MIN_LENGTH_M,
    unique_id_fields: "tuple | list" = (),
) -> ResultatDecoupe:
    """
    Exécute la découpe de *feature* dans *layer*.

    L'appelant doit :
      - avoir la couche en mode édition
      - encadrer cet appel par ``layer.beginEditCommand`` / ``layer.endEditCommand``
        (ou ``layer.destroyEditCommand`` en cas d'échec)

    Paramètres
    ----------
    layer            : QgsVectorLayer en mode édition
    feature          : objet à couper (référence en lecture seule — non modifié en place)
    cut_pt           : QgsPoint du point de coupe (Z déjà interpolé)
    seg_idx          : index 0-basé du segment (depuis geometrie.trouver_point_coupe)
    is_vertex        : True si cut_pt est un sommet existant
    min_length       : longueur minimale acceptable pour chaque tronçon résultant (unités carte)
    unique_id_fields : noms des champs uniques dans la couche (ex. ``cleabs``).
                       La valeur est conservée sur le tronçon *le plus long* et effacée
                       sur le plus court afin que le fournisseur attribue un nouvel
                       identifiant.

    Retourne
    --------
    ResultatDecoupe en cas de succès. Lève ErreurDecoupe pour tout échec récupérable.
    """
    if not layer.isEditable():
        raise ErreurDecoupe("La couche n'est pas en mode édition.")

    # Découpage géométrique — retourne les géométries des deux tronçons ou None en cas d'échec (ex. géométrie non linéaire, point de coupe invalide, etc.)
    split_result = couper_ligne_au_point(feature.geometry(), cut_pt, seg_idx, is_vertex)
    if split_result is None:
        raise ErreurDecoupe(
            "Impossible de découper la géométrie. "
            "Vérifiez que l'objet est un tronçon linéaire simple."
        )

    geom1, geom2 = split_result

    # Garde de longueur minimale  (toujours en mètres via QgsDistanceArea)
    da = QgsDistanceArea()
    da.setSourceCrs(layer.crs(), QgsProject.instance().transformContext())
    da.setEllipsoid(QgsProject.instance().ellipsoid() or "GRS80")

    length_1 = da.measureLength(geom1)   # mètres
    length_2 = da.measureLength(geom2)   # mètres

    if length_1 < min_length:
        raise ErreurDecoupe(
            f"Découpe refusée : le premier tronçon résultant ferait {length_1:.2f} m "
            f"(minimum autorisé : {min_length} m)."
        )
    if length_2 < min_length:
        raise ErreurDecoupe(
            f"Découpe refusée : le second tronçon résultant ferait {length_2:.2f} m "
            f"(minimum autorisé : {min_length} m)."
        )

    total_length = length_1 + length_2

    # On s'assure que feat1 (la ligne UPDATE qui conserve les attributs d'origine) est toujours
    # le *plus long* des deux tronçons. Si geom2 est plus long, on échange les géométries
    # et leur longueur mesurée afin que la suite du code soit identique.
    if length_2 > length_1:
        geom1, geom2 = geom2, geom1
        length_1, length_2 = length_2, length_1

    # Construction des deux objets QgsFeature de sortie
    # --- Objet 1 : l'objet d'origine, géométrie réduite à la partie 1 ---
    feat1 = QgsFeature(feature)           # copie le FID + tous les attributs
    feat1.setGeometry(geom1)

    # --- Objet 2 : nouvel objet, attributs assainis, géométrie = partie 2 ---
    feat2 = QgsFeature(feature.fields())  # sans FID — sera attribué par le fournisseur à l'insertion
    feat2.setGeometry(geom2)
    _copy_attributes_sanitized(feat2, feature)

    # Champs BDUni bornes postales — vidés sur le nouveau tronçon (le plus court).
    # Le serveur BDUni recalcule les BP la nuit sur les deux objets via le mécanisme LOS/BAN.
    if _is_bduni_troncon(layer.name()):
        for _bp_f in _BP_FIELDS:
            _bp_idx = feat2.fields().indexOf(_bp_f)
            if _bp_idx >= 0:
                feat2.setAttribute(_bp_idx, None)

    # Champs identifiants uniques — toujours vidés sur le nouvel objet INSERT.
    #
    #     L'API distingue UPDATE et INSERT par le signe du FID QGIS :
    #       feat1  FID positif  → UPDATE  → DOIT conserver l'identifiant
    #       feat2  FID négatif  → INSERT  → NE DOIT PAS porter l'ID d'origine
    #
    #     La longueur est indirectement déterminante : le swap ci-dessus garantit
    #     que feat1 est toujours le tronçon le plus long, et c'est lui qui conserve
    #     l'identifiant de la ligne de BASE DE DONNÉES existante.
    #
    #     Si le champ est NOT NULL au niveau du schéma DB, on utilise "" au lieu
    #     de NULL afin que SpatiaLite accepte l'INSERT sans violation de contrainte.
    #     L'API BDUni/gcms interprète une chaîne vide comme « pas d'identifiant
    #     fourni » et en attribue un nouveau côté serveur à l'INSERT.
    for field_name in unique_id_fields:
        uf_idx = layer.fields().indexOf(field_name)
        if uf_idx < 0:
            continue
        not_null = _field_is_not_null(layer, field_name, uf_idx)
        feat2.setAttribute(uf_idx, "" if not_null else None)


    if not layer.changeGeometry(feature.id(), geom1):
        raise ErreurDecoupe(
            "Impossible de modifier la géométrie de l'objet existant. "
            "Vérifiez que la couche est éditable et que l'objet n'est pas protégé."
        )

    # Ajout du nouvel objet
    if not layer.addFeature(feat2):
        raise ErreurDecoupe(
            "Impossible de créer le nouvel objet dans la couche."
        )

    # Récupération du FID attribué par le fournisseur
    new_fid = feat2.id()

    return ResultatDecoupe(
        original_fid=feature.id(),
        new_fid=new_fid,
        feature_1=feat1,
        feature_2=feat2,
        length_1=length_1,
        length_2=length_2,
    )


# Fonctions utilitaires
def _copy_attributes_sanitized(dest: QgsFeature, src: QgsFeature) -> None:
    """
    Copie tous les attributs de *src* vers *dest* en appliquant deux passes de nettoyage :

    1. Les champs clés primaires auto-assignés par SpatiaLite (``id_sqlite_*``)
       sont mis à NULL afin que la base de données attribue une nouvelle PK à
       l'insertion. Copier la valeur d'origine déclencherait une violation de
       contrainte UNIQUE.

    2. Les valeurs datetime qui comportent une précision sub-seconde sont tronquées
       à la seconde entière car l'API BDUni rejette les formats milli/microseconde.
       Géré aussi bien pour les valeurs Python ``str`` (suppression par regex)
       que pour les objets ``QDateTime`` (reconstruction QTime sans composante
       sub-seconde).
    """
    fields = src.fields()
    values = list(src.attributes())

    for i, f in enumerate(fields):
        val = values[i]

        # --- Effacement des PK auto-assignées par SpatiaLite ----------
        if _SQLITE_PK_RE.match(f.name()):
            values[i] = None
            continue

        # --- Normalisation datetime — suppression de la précision sub-seconde ---
        # Les valeurs peuvent être des str Python OU des QDateTime selon le fournisseur.
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
    Retourne True si *field_name* porte une contrainte NOT NULL à N'IMPORTE QUEL niveau :
    — Champs du fournisseur (métadonnées du schéma DB rapportées par le fournisseur de données)
    — Champs de la couche   (configuration du formulaire d'attributs QGIS)
    — Repli via PRAGMA SQLite table_info (pour les couches SpatiaLite où le fournisseur
      ne propage pas les métadonnées NOT NULL, par ex. couche chargée par un plugin tiers)
    """
    # Champs du fournisseur (schéma de base de données via l'API QGIS)
    pf_idx = layer.dataProvider().fields().indexOf(field_name)
    if pf_idx >= 0:
        if (layer.dataProvider().fields().field(pf_idx).constraints().constraints()
                & QgsFieldConstraints.ConstraintNotNull):
            return True

    # Champs de la couche (configuration du formulaire d'attributs)
    if (layer.fields().field(uf_idx).constraints().constraints()
            & QgsFieldConstraints.ConstraintNotNull):
        return True

    # Requête directe sur le schéma SQLite — solution de repli fiable pour les couches
    #    SpatiaLite où le fournisseur ne propage pas les métadonnées NOT NULL.
    try:
        uri = QgsDataSourceUri(layer.dataProvider().dataSourceUri())
        db_path = uri.database()
        table   = uri.table()
        if db_path and table:
            conn = sqlite3.connect(db_path)
            try:
                for row in conn.execute(f"PRAGMA table_info([{table}])"):
                    # row: (cid, name, type, notnull, dflt_value, pk)
                    if row[1] == field_name:
                        return bool(row[3])
            finally:
                conn.close()
    except Exception:
        pass

    return False



