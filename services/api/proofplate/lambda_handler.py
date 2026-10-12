"""AWS Lambda entry point (API Gateway HTTP API, payload format 2.0)."""
from mangum import Mangum

from .main import app

handler = Mangum(app, lifespan="off")
