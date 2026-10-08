import pytest

from corewatch.config import ConfigError, parse_config

BASE = {"databases": [{"name": "CORE", "host": "db1", "user": "mon", "password_env": "PW"}]}


def test_defaults_and_dsn():
    cfg = parse_config(BASE, env={"PW": "s3cret"})
    db = cfg.databases[0]
    assert (cfg.port, db.port, cfg.cache_seconds) == (9953, 50000, 30.0)
    assert db.dsn() == (
        "DATABASE=CORE;HOSTNAME=db1;PORT=50000;PROTOCOL=TCPIP;UID=mon;PWD=s3cret;ConnectTimeout=10;"
    )


def test_ssl_adds_security_keyword():
    raw = {"databases": [dict(BASE["databases"][0], ssl=True)]}
    assert "SECURITY=SSL;" in parse_config(raw, env={"PW": "x"}).databases[0].dsn()


def test_password_in_yaml_is_refused():
    raw = {"databases": [{"name": "CORE", "host": "db1", "user": "mon", "password": "oops"}]}
    with pytest.raises(ConfigError, match="password_env"):
        parse_config(raw, env={})


def test_missing_env_var():
    with pytest.raises(ConfigError, match="PW is not set"):
        parse_config(BASE, env={})


def test_password_file(tmp_path):
    secret = tmp_path / "pw"
    secret.write_text("from-file\n")
    raw = {"databases": [{"name": "CORE", "host": "db1", "user": "mon", "password_file": str(secret)}]}
    assert parse_config(raw, env={}).databases[0].password == "from-file"


def test_no_databases():
    with pytest.raises(ConfigError, match="no databases"):
        parse_config({}, env={})


def test_duplicate_database():
    raw = {"databases": BASE["databases"] * 2}
    with pytest.raises(ConfigError, match="listed twice"):
        parse_config(raw, env={"PW": "x"})


def test_label_names_are_the_union():
    raw = {
        "databases": [
            dict(BASE["databases"][0], labels={"client": "a"}),
            dict(BASE["databases"][0], name="QA", labels={"environment": "qa"}),
        ]
    }
    assert parse_config(raw, env={"PW": "x"}).label_names == ["client", "environment"]
