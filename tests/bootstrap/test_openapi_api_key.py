from fastapi import Depends, FastAPI

from src.bootstrap.openapi import API_KEY_SCHEME, SESSION_SCHEME, add_api_key_scheme, install_api_key_scheme
from src.platform.security.current_user import oauth2_scheme


def build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/private")
    async def private(token: str = Depends(oauth2_scheme)):
        return {}

    @app.get("/open")
    async def public():
        return {}

    install_api_key_scheme(app)
    return app


def test_the_schema_declares_a_bearer_scheme_the_authorize_button_accepts():
    scheme = build_app().openapi()["components"]["securitySchemes"][API_KEY_SCHEME]

    assert scheme["type"] == "http"
    assert scheme["scheme"] == "bearer"
    assert "pui_" in scheme["description"]


def test_a_route_that_takes_the_session_token_also_takes_a_key():
    schema = build_app().openapi()

    assert schema["paths"]["/private"]["get"]["security"] == [{SESSION_SCHEME: []}, {API_KEY_SCHEME: []}]


def test_a_route_without_authentication_stays_without_it():
    assert "security" not in build_app().openapi()["paths"]["/open"]["get"]


def test_the_session_scheme_is_still_declared():
    assert SESSION_SCHEME in build_app().openapi()["components"]["securitySchemes"]


def test_asking_twice_does_not_add_the_alternative_twice():
    app = build_app()
    app.openapi()

    assert app.openapi()["paths"]["/private"]["get"]["security"].count({API_KEY_SCHEME: []}) == 1


def test_patching_a_schema_twice_changes_nothing_the_second_time():
    schema = {"paths": {"/a": {"get": {"security": [{SESSION_SCHEME: []}]}}}}
    once = add_api_key_scheme(schema)
    snapshot = repr(once)

    assert repr(add_api_key_scheme(once)) == snapshot
