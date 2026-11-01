import time

import pytest

from painpoint import db
from painpoint.config import load_config

from data import make_posts


@pytest.fixture
def cfg(tmp_path):
    c = load_config()
    c["db_path"] = str(tmp_path / "test.db")
    c["llm"]["workers"] = 2
    return c


@pytest.fixture
def conn(cfg):
    c = db.connect(cfg["db_path"])
    db.init(c)
    yield c
    c.close()


@pytest.fixture
def now():
    return time.time()


@pytest.fixture
def loaded(conn, now):
    """Database with the synthetic posts inserted (no extraction yet)."""
    db.insert_posts(conn, make_posts(now))
    return conn
