"""HTTP surface. Two isolated workspaces share the same use cases:

  /api/demo/<op>   public, stateless. The server rebuilds an in-memory
                   workspace from the fixed simulated seed plus the caller's
                   own browser-held sandbox journal, computes, and forgets.
                   It has no handle on the pilot store at all.
  /api/pilot/<op>  authenticated. The site is looked up from the caller's
                   verified identity; a client-supplied tenant id is never read.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, create_model

from . import __version__, service, transfer
from .domain.rules import RuleError
from .model.demand import MODEL_VERSION
from .repositories.memory import MemoryRepository

ENV = os.environ.get("PROOFPLATE_ENV", "local")          # local | aws
AUTH = os.environ.get("PROOFPLATE_AUTH", "dev")          # dev | cognito
STORE = os.environ.get("PROOFPLATE_STORE", "sqlite")     # sqlite | dynamo | memory
ASSETS = Path(__file__).parent / "assets"
MAX_SANDBOX = 900
MAX_DEMO_BODY = 700_000

if ENV != "local" and AUTH != "cognito":
    # Fail closed: a deployed stack must never run with the development identity.
    raise RuntimeError("PROOFPLATE_AUTH must be 'cognito' outside local development")

log = logging.getLogger("proofplate")
log.setLevel(logging.INFO)
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(title="ProofPlate API", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
OPS = {**service.OPS, **transfer.OPS}


# ---------------------------------------------------------------- storage
@lru_cache(maxsize=1)
def pilot_repo():
    if STORE == "dynamo":
        from .repositories.dynamo_repo import DynamoRepository

        return DynamoRepository(os.environ["PROOFPLATE_TABLE"])
    if STORE == "memory":
        return MemoryRepository()
    from .repositories.sqlite_repo import SqliteRepository

    return SqliteRepository(os.environ.get("PROOFPLATE_DB", "proofplate-local.db"))


@lru_cache(maxsize=1)
def _seed() -> dict:
    return json.loads((ASSETS / "demo_seed.json").read_text(encoding="utf-8"))


def demo_repo() -> MemoryRepository:
    seed = _seed()
    repo = MemoryRepository()
    site = seed["site"]
    for rec in seed["records"]:
        repo.records[(site, rec["kind"], rec["id"])] = rec["data"]
    bucket = repo.events.setdefault(site, {})
    for ev in seed["events"]:
        bucket[ev["event_id"]] = ev
        key = (site, f"{ev['session_id']}|{ev['diner_token']}|{ev['food_id']}")
        repo.counts[key] = repo.counts.get(key, 0) + 1
    return repo


# ---------------------------------------------------------------- identity
def identify(request: Request) -> tuple[str, str]:
    """Return (user_id, display). Raises 401 when there is no verified identity."""
    if AUTH == "cognito":
        claims = (((request.scope.get("aws.event") or {}).get("requestContext") or {}).get("authorizer") or {}).get("jwt", {}).get("claims")
        if not claims or not claims.get("sub"):
            raise RuleError("UNAUTHENTICATED", "Sign in to use the pilot workspace.", 401)
        return str(claims["sub"]), str(claims.get("email") or claims.get("cognito:username") or claims["sub"])
    user = request.headers.get("x-dev-user", "").strip()
    if not user or not user.replace("-", "").replace("_", "").isalnum() or len(user) > 40:
        raise RuleError("UNAUTHENTICATED", "Sign in to use the pilot workspace.", 401)
    return f"dev:{user}", f"{user} (local dev)"


def pilot_ctx(request: Request) -> service.Ctx:
    user_id, display = identify(request)
    repo = pilot_repo()
    members = repo.memberships(user_id)
    if not members:
        raise RuleError("NOT_A_MEMBER", "Your account is not attached to a dining site yet. Ask the site manager to add you.", 403)
    m = sorted(members, key=lambda x: x["site"])[0]
    return service.Ctx(repo=repo, site_id=m["site"], operator=display, role=m["role"], mode="pilot")


# ---------------------------------------------------------------- errors
@app.exception_handler(RuleError)
async def _rule_error(_: Request, exc: RuleError):
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def _validation(_: Request, exc: RequestValidationError):
    fields = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()[:20]]
    first = fields[0] if fields else {"field": "", "message": "invalid request"}
    return JSONResponse({"error": {"code": "VALIDATION", "message": f"{first['field']}: {first['message']}", "fields": fields}}, status_code=422)


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError):
    return JSONResponse({"error": {"code": "INVALID", "message": str(exc)}}, status_code=422)


@app.middleware("http")
async def _observe(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    t0 = time.perf_counter()
    if request.url.path.startswith("/api/demo/") and int(request.headers.get("content-length") or 0) > MAX_DEMO_BODY:
        return JSONResponse({"error": {"code": "TOO_LARGE", "message": "Demo sandbox is too large. Reset the demo."}}, status_code=413)
    try:
        response = await call_next(request)
    except Exception:  # never leak internals; never log payloads
        log.exception(json.dumps({"rid": rid, "path": request.url.path, "outcome": "unhandled"}))
        response = JSONResponse({"error": {"code": "INTERNAL", "message": "Something went wrong on the server.", "request_id": rid}}, status_code=500)
    ms = round((time.perf_counter() - t0) * 1000, 1)
    response.headers["x-request-id"] = rid
    response.headers["server-timing"] = f"app;dur={ms}"
    if request.url.path.startswith("/api/"):
        response.headers["cache-control"] = "no-store"
        log.info(json.dumps({"rid": rid, "method": request.method, "path": request.url.path, "status": response.status_code, "ms": ms}))
    return response


# ---------------------------------------------------------------- public
@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True, "version": __version__, "model_version": MODEL_VERSION, "env": ENV, "store": STORE,
        "region": os.environ.get("AWS_REGION"), "function": os.environ.get("AWS_LAMBDA_FUNCTION_NAME"),
        "revision": os.environ.get("PROOFPLATE_REVISION", "dev"),
    }


@app.get("/api/config")
def config() -> dict:
    """Runtime configuration for the web app, taken from the deployed stack."""
    return {
        "auth": AUTH,
        "cognito": {"region": os.environ.get("AWS_REGION"), "userPoolId": os.environ.get("PROOFPLATE_USER_POOL_ID"),
                    "clientId": os.environ.get("PROOFPLATE_USER_POOL_CLIENT_ID")} if AUTH == "cognito" else None,
        "aws": {"region": os.environ.get("AWS_REGION"), "function": os.environ.get("AWS_LAMBDA_FUNCTION_NAME"),
                "table": os.environ.get("PROOFPLATE_TABLE")} if ENV == "aws" else None,
        "version": __version__, "revision": os.environ.get("PROOFPLATE_REVISION", "dev"),
    }


@app.get("/api/evaluation")
def evaluation() -> Any:
    path = ASSETS / "evaluation.json"
    if not path.exists():
        raise RuleError("NO_EVALUATION", "No evaluation artifact has been generated yet.", 404)
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/demo/seed")
def seed_info() -> dict:
    return _seed()["manifest"]


# ---------------------------------------------------------------- ops
def _register(name: str, args_model: type[BaseModel], fn, writes: bool) -> None:
    strict = ConfigDict(extra="forbid")  # e.g. a client-supplied site id is rejected, not ignored
    DemoBody = create_model(f"Demo_{name}", __config__=strict, args=(args_model, ...),
                            sandbox=(list[dict], Field(default_factory=list, max_length=MAX_SANDBOX)))
    PilotBody = create_model(f"Pilot_{name}", __config__=strict, args=(args_model, ...))

    def demo_endpoint(body):
        repo = demo_repo()
        repo.replay("demo", body.sandbox)
        repo.journal = []
        ctx = service.Ctx(repo=repo, site_id="demo", operator="demo-visitor", role="demo", mode="demo")
        result = fn(ctx, body.args)
        return {"result": result, "sandbox_append": repo.journal}

    def pilot_endpoint(body, ctx=Depends(pilot_ctx)):
        return {"result": fn(ctx, body.args)}

    # The body models are created per operation, so annotations are attached
    # as objects (string annotations could not be resolved from this scope).
    demo_endpoint.__annotations__ = {"body": DemoBody, "return": dict}
    pilot_endpoint.__annotations__ = {"body": PilotBody, "ctx": service.Ctx, "return": dict}

    app.post(f"/api/demo/{name}", name=f"demo_{name}", tags=["demo"])(demo_endpoint)
    app.post(f"/api/pilot/{name}", name=f"pilot_{name}", tags=["pilot"])(pilot_endpoint)


for _name, (_model, _fn, _writes) in OPS.items():
    _register(_name, _model, _fn, _writes)


# ---------------------------------------------------------------- static fallback
STATIC = Path(os.environ.get("PROOFPLATE_STATIC", str(Path(__file__).parent / "static")))
if STATIC.is_dir() and (STATIC / "index.html").exists():
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"error": {"code": "NOT_FOUND", "message": "No such API route."}}, status_code=404)
        target = (STATIC / path).resolve()
        if path and target.is_file() and STATIC.resolve() in target.parents:
            return FileResponse(target)
        if "." in path.rsplit("/", 1)[-1]:  # a missing asset is an error, not the app shell
            return JSONResponse({"error": {"code": "NOT_FOUND", "message": "No such file."}}, status_code=404)
        return FileResponse(STATIC / "index.html", headers={"cache-control": "no-cache"})
