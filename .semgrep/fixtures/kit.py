# ruff: noqa
# mypy: ignore-errors
# Semgrep fixture: deliberately insecure code for `make semgrep-test`.
import logging

import modal
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

app = FastAPI()
row: list[str] = []
email = ""
prompt = ""
request_id = ""
len_prompt_chars = 0
# ruleid: kit-cors-wildcard
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["POST"])
# ok: kit-cors-wildcard
app.add_middleware(CORSMiddleware, allow_origins=["https://example.com"])
# ruleid: kit-cors-wildcard
HEADERS = {"Access-Control-Allow-Origin": "*"}
# ok: kit-cors-wildcard
SAFE_HEADERS = {"Access-Control-Allow-Origin": "https://example.com"}


def client_ip(request):
    # ruleid: kit-xff-header
    return request.headers.get("X-Forwarded-For", "")


def call_provider(client, body, request):
    try:
        client.call()
    except Exception as err:
        # ruleid: kit-log-exception-text
        logger.warning("provider failed: {}", err)
        # ruleid: kit-log-exception-text
        logger.warning(f"provider failed: {err.response.text}")
        # ruleid: kit-log-exception-text
        logging.error("failed %s", str(err))
        # ok: kit-log-exception-text
        logger.warning("provider failed: {}", type(err).__name__)
        # ok: kit-log-exception-text
        logger.warning("provider failed: {}", err.status_code)
        msg = str(err)
        # ruleid: kit-log-exception-text
        logger.warning(msg)
    # ruleid: kit-log-sensitive-arg
    logger.info("got {}", body)
    # ruleid: kit-log-sensitive-arg
    logger.exception("append_row failed; row was: {}", row)
    # ruleid: kit-log-sensitive-arg
    logger.info("visitor {}", request.json())
    # ok: kit-log-sensitive-arg
    logger.info("append_row failed for request {}", request_id)
    # ok: kit-log-sensitive-arg
    logger.info("prompt length {}", len_prompt_chars)
    # ok: kit-log-sensitive-arg
    logger.info("body length {}", len(body))
    # ruleid: kit-log-sensitive-arg
    logger.info(f"sent to {email}")
    # ruleid: kit-log-sensitive-arg
    logger.info("sent to " + email)
    # ruleid: kit-log-sensitive-arg
    log = logger.bind(email=email)
    # ruleid: kit-log-sensitive-arg
    print(prompt)
    log.info("bound")


# ruleid: kit-modal-unpinned-install
image = modal.Image.debian_slim().pip_install("fastapi[standard]", "anthropic")
# ruleid: kit-modal-unpinned-install
image2 = modal.Image.debian_slim().uv_pip_install(["loguru"])
# ok: kit-modal-unpinned-install
image3 = modal.Image.debian_slim().uv_pip_install("fastapi==0.141.1", "loguru==0.7.3")
# ok: kit-modal-unpinned-install
image4 = modal.Image.debian_slim(python_version="3.12").uv_sync("api")
