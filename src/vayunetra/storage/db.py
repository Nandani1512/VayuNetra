from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from vayunetra.common.config import get_settings


def _sync_url(url: str) -> str:
    # psycopg works for both sync and async; keep the driver but make sure the
    # async URL uses the right dialect.
    return url.replace("+asyncpg", "+psycopg")


def _async_url(url: str) -> str:
    if "+psycopg" in url:
        return url  # psycopg3 supports async
    if "postgresql://" in url and "+" not in url:
        return url.replace("postgresql://", "postgresql+psycopg://")
    return url


_engine: Engine | None = None
_async_engine: AsyncEngine | None = None
_SyncSession: sessionmaker[Session] | None = None
_AsyncSessionMaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> Engine:
    global _engine, _SyncSession
    if _engine is None:
        _engine = create_engine(_sync_url(get_settings().database_url), pool_pre_ping=True)
        _SyncSession = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_async_engine() -> AsyncEngine:
    global _async_engine, _AsyncSessionMaker
    if _async_engine is None:
        _async_engine = create_async_engine(
            _async_url(get_settings().database_url), pool_pre_ping=True
        )
        _AsyncSessionMaker = async_sessionmaker(_async_engine, expire_on_commit=False)
    return _async_engine


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SyncSession is not None
    s = _SyncSession()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@asynccontextmanager
async def async_session_scope() -> AsyncIterator[AsyncSession]:
    get_async_engine()
    assert _AsyncSessionMaker is not None
    async with _AsyncSessionMaker() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
