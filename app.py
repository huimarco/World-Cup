"""FastAPI app (flat layout — all files in one folder).

A SINGLE background task polls the upstream APIs on an interval and writes to an
in-memory cache. Every browser tab hits OUR /api/odds (which just reads the
cache), so upstream load is constant no matter how many people view the
dashboard — keeping us well under every rate limit.

Run from the folder that contains this file:
    uvicorn app:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import contextlib
import ssl
from pathlib import Path

import aiohttp
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
import sources

HERE = Path(__file__).resolve().parent  # index.html lives next to this file


def _ssl_context():
    """Build an SSL context that can verify certs on Windows / corporate
    networks. Prefers the OS trust store (which includes any company root CA
    your IT installed), then falls back to the certifi bundle."""
    try:
        import truststore  # uses the Windows/macOS system trust store
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()

_cache: dict = {"matches": [], "errors": {}, "updated": None}
_lock = asyncio.Lock()


async def _poller():
    timeout = aiohttp.ClientTimeout(total=config.HTTP_TIMEOUT_SECONDS)
    headers = {"User-Agent": "worldcup-odds-dashboard/1.0"}
    connector = aiohttp.TCPConnector(ssl=_ssl_context())
    async with aiohttp.ClientSession(
        timeout=timeout, headers=headers, connector=connector
    ) as session:
        while True:
            try:
                data = await sources.gather_all(session)
                async with _lock:
                    if data["matches"] or not _cache["matches"]:
                        _cache.update(data)
                    else:
                        _cache["errors"] = data["errors"]
                        _cache["updated"] = data["updated"]
            except Exception as e:  # noqa: BLE001 - never let the loop die
                async with _lock:
                    _cache["errors"] = {"poller": str(e)}
            await asyncio.sleep(config.POLL_INTERVAL_SECONDS)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_poller())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="World Cup 2026 Odds", lifespan=lifespan)


@app.get("/api/odds")
async def odds():
    async with _lock:
        return JSONResponse(dict(_cache))


@app.get("/api/health")
async def health():
    async with _lock:
        return {
            "ok": True,
            "matches": len(_cache["matches"]),
            "updated": _cache["updated"],
            "errors": _cache["errors"],
            "poll_interval": config.POLL_INTERVAL_SECONDS,
        }


@app.get("/")
async def index():
    return FileResponse(HERE / "index.html")


app.mount("/static", StaticFiles(directory=HERE), name="static")
