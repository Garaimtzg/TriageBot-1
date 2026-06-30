"""Tests útiles que cubren huecos reales tras externalizar la configuración.

1. Los endpoints HTMX (`GET /`, `POST /ui/tickets`, `GET /ui/tickets`) no tenían
   ninguna cobertura: son la cara visible del producto.
2. El loader de configuración respeta `CONFIG_PATH` (mecanismo central del PR).
3. La configuración de `config.yaml` es la ÚNICA fuente de verdad: ningún valor
   se ha vuelto a hardcodear en los módulos (guarda contra regresiones).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import classifier, models
from app.config import get_config
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient con una base de datos aislada por test."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    return TestClient(app)


def test_htmx_ui_create_and_filter(client, monkeypatch):
    """El formulario HTMX crea el ticket y la tabla devuelta refleja el filtro."""
    monkeypatch.setattr(
        "app.classifier.classify_ticket",
        lambda title, description: {"category": "bug", "priority": "P2", "tags": ["login"]},
    )

    # GET / devuelve la página con el formulario.
    home = client.get("/")
    assert home.status_code == 200
    assert "TriageBot" in home.text
    assert 'hx-post="/ui/tickets"' in home.text

    # POST del formulario crea el ticket y devuelve el fragmento de tabla con él.
    created = client.post(
        "/ui/tickets",
        data={"title": "La app no carga", "description": "Pantalla en blanco al entrar"},
    )
    assert created.status_code == 200
    assert "La app no carga" in created.text

    # Filtrar por una categoría que no coincide muestra el estado vacío...
    empty = client.get("/ui/tickets", params={"category": "feature_request"})
    assert empty.status_code == 200
    assert "La app no carga" not in empty.text
    assert "No hay tickets" in empty.text

    # ...y filtrar por la categoría correcta sí lo incluye.
    match = client.get("/ui/tickets", params={"category": "bug"})
    assert "La app no carga" in match.text

    # Input inválido desde el formulario devuelve 422 (no se cae).
    invalid = client.post("/ui/tickets", data={"title": "   ", "description": "x"})
    assert invalid.status_code == 422


def test_config_path_override_is_honored(tmp_path, monkeypatch):
    """get_config() lee la ruta indicada en CONFIG_PATH, no sólo el fichero por defecto."""
    custom = tmp_path / "custom.yaml"
    custom.write_text("ticket:\n  categories: [solo_esta]\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(custom))

    get_config.cache_clear()
    try:
        assert get_config()["ticket"]["categories"] == ["solo_esta"]
    finally:
        # Vaciar la caché para que el resto de tests vuelvan a leer el config real.
        get_config.cache_clear()


def test_config_yaml_is_single_source_of_truth():
    """Los módulos toman sus valores de config.yaml: nada se ha re-hardcodeado."""
    cfg = get_config()
    cls = cfg["classifier"]

    # Clasificador: modelo, prompt, parámetros y nombre de la env var de la key.
    assert classifier.MODEL == cls["model"]
    assert classifier.BASE_URL == cls["base_url"]
    assert classifier.MAX_TOKENS == cls["max_tokens"]
    assert classifier.API_KEY_ENV == cls["api_key_env"]
    assert classifier.SYSTEM_PROMPT == cls["system_prompt"]
    assert classifier.FALLBACK_CLASSIFICATION == cls["fallback"]

    # Catálogos y límites de validación.
    assert models.ALLOWED_CATEGORIES == set(cfg["ticket"]["categories"])
    assert models.ALLOWED_PRIORITIES == set(cfg["ticket"]["priorities"])
    assert models.ALLOWED_STATUSES == set(cfg["ticket"]["statuses"])
    assert models.TITLE_MAX_LEN == cfg["validation"]["title_max_len"]
    assert models.DESCRIPTION_MAX_LEN == cfg["validation"]["description_max_len"]
