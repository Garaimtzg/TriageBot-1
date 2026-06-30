"""Carga de la configuración de TriageBot desde ``config.yaml``.

Centraliza todo lo que antes estaba hardcodeado (modelo, prompt, parámetros del
clasificador, límites de validación, catálogos...). Ningún secreto vive aquí:
las API keys se leen de variables de entorno cuyos *nombres* se declaran en el
propio YAML.

La ruta del fichero puede sobreescribirse con la variable de entorno
``CONFIG_PATH`` (útil en tests o despliegues).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.yaml"


@lru_cache(maxsize=1)
def get_config() -> dict[str, Any]:
    """Lee y cachea ``config.yaml``.

    Returns the parsed mapping. Raises ``FileNotFoundError`` if the file is
    missing or ``ValueError`` if its top level is not a mapping.
    """
    path = Path(os.getenv("CONFIG_PATH", str(DEFAULT_CONFIG_PATH)))
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Configuración inválida en {path}: se esperaba un mapa YAML")
    return data
