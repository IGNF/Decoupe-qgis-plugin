# Outil Découpe IGN — Plugin QGIS

Plugin QGIS de découpe métier développé par l'IGN dans le cadre de la migration BDUni.  
Il fournit deux outils interactifs : la **découpe de tronçon linéaire** et la **découpe de polygone**, accessibles depuis la barre d'outils QGIS.

---

## Description

### Contexte

Ce plugin a été conçu pour répondre au besoin de saisie terrain BDUni : découper des objets vectoriels (tronçons de route, polygones) directement dans QGIS, en conservant la cohérence des attributs métier (identifiants uniques, plages d'adresses, dates, clés SpatiaLite).

Ce plugin a été conçu pour permettre la découpe des couches BDUni (vectoriels : tronçons de routes, polygones) tout en conservant la cohérence des attributs métier (identifiants uniques, cleabs...).

### Fonctionnalités

| Outil | Description |
|---|---|
| **Découpe tronçon** | Coupe une entité linéaire en deux au point cliqué |
| **Découpe polygone** | Coupe un polygone selon une ligne tracée librement |
| **Paramètres** | Configure les champs identifiants uniques à préserver |

---

## Prérequis

- **QGIS** ≥ 3.16
- Python 3.x (fourni avec QGIS)
- Aucune dépendance externe — uniquement les API QGIS et Qt standard
- Compatible Qt5/6

---


## Utilisation

Une barre d'outils **« Découpe Tronçon »** apparaît dans QGIS. Les outils sont également accessibles via le menu **Vecteur → Outil Découpe Tronçon**.

### Découpe d'un tronçon linéaire

> Outil : icône **ciseaux route** (ou **Vecteur → Outil Découpe Tronçon → Découper un tronçon**)

1. **Activer l'outil** en cliquant sur le bouton (curseur en croix).
2. **Clic gauche** sur un objet linéaire : le tronçon se surligne en orange, un marqueur rouge indique le point de coupe courant. Si le curseur est proche d'un sommet existant (tolérance : 15 px), l'accrochage s'active automatiquement (marqueur orange).
3. **Déplacer la souris** pour ajuster précisément le point de coupe le long du tronçon.
4. **Clic droit** pour confirmer et exécuter la découpe.

**Résultat :**
- Le tronçon **le plus long** conserve le FID d'origine et tous ses attributs.
- Un **nouveau tronçon** est créé (INSERT) avec les mêmes attributs, à l'exception des champs identifiants uniques (vidés pour permettre l'attribution d'une nouvelle valeur par le serveur).
- La coordonnée Z est **interpolée linéairement** au point de coupe.
- La découpe est **refusée** si l'un des tronçons résultants est inférieur à **2 mètres**.

### Découpe d'un polygone

> Outil : icône **découpe de parties** (ou **Vecteur → Outil Découpe Tronçon → Découper un polygone**)

1. **Activer l'outil** en cliquant sur le bouton.
2. **Clic gauche sur le polygone** à découper : il se surligne en orange.
3. **Clics gauches successifs** pour tracer la ligne de coupe (au moins 2 points). La ligne en cours est prévisualisée en rouge.
4. **Clic droit** (avec au moins 2 points posés) pour confirmer et exécuter la découpe.

**Résultat :**
- Le **plus grand polygone** résultant hérite du FID et des attributs d'origine.
- Les **autres polygones** sont créés en INSERT avec les attributs assainis.
- La découpe est **refusée** si l'un des polygones résultants a une surface inférieure à **1 m²**.
- La ligne de coupe doit **traverser complètement** le polygone (entrer et sortir).

**Annulation :** la touche `Échap` annule l'opération en cours et réinitialise l'outil.

### Paramètres

> Bouton engrenage dans la barre d'outils

Permet de configurer les **champs identifiants uniques** de la couche active (ex. `cleabs`).  
Après une découpe, ces champs sont conservés sur l'objet le plus long/grand et **vidés** sur les nouveaux objets pour que le serveur leur attribue automatiquement un nouvel identifiant.

La configuration est persistante via `QgsSettings` et s'applique à toutes les couches.

---

## Comportements métier spécifiques (BDUni)

| Situation | Comportement |
|---|---|
| Couche `troncon_de_route` | Les champs de plages d'adresses (`borne_debut_droite`, `borne_debut_gauche`, `borne_fin_droite`, `borne_fin_gauche`, `bornes_debut_interpolees`, `bornes_fin_interpolees`) sont **automatiquement vidés** sur le nouveau tronçon. Le recalcul est assuré par le mécanisme LOS/BAN côté serveur. |
---

## Architecture du code

```
Outil_Decoupe_QGIS/
├── __init__.py                          # Point d'entrée QGIS (classFactory)
├── plugin.py                            # Classe principale — cycle de vie, barre d'outils
├── metadata.txt                         # Métadonnées du plugin (version, auteur, etc.)
├── cutting_road.png                     # Icône de l'outil découpe tronçon
│
├── interface/
│   ├── __init__.py
│   ├── dialogue_parametres.py           # Fenêtre de configuration des champs uniques
│   ├── outil_decoupe_carte.py           # QgsMapTool — découpe linéaire interactive
│   └── outil_decoupe_polygone_carte.py  # QgsMapTool — découpe polygone interactive
│
└── traitement/
    ├── __init__.py
    ├── geometrie.py                     # Calcul du point de coupe, projection, interpolation Z
    ├── moteur_decoupe.py                # Logique métier de découpe linéaire
    ├── moteur_decoupe_polygone.py       # Logique métier de découpe polygone
    └── parametres.py                   # Lecture/écriture des paramètres (QgsSettings)
```


## Contacts

| Nom | Prénom | Mail | Fonction |
| Mortier | Melanie | | melanie.mortier@ign.fr | Chef de projet |
| De Bock | Axel | axel.debock@ign.fr | Concepteur Développeur |
