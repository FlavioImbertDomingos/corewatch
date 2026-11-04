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


def test_informix_target_defaults():
    raw = {"databases": [{"name": "ifx_prod", "engine": "informix", "host": "ifx1", "user": "corewatch",
                          "password_env": "PW"}]}
    db = parse_config(raw, env={"PW": "x"}).databases[0]
    assert (db.engine, db.port, db.connect_database) == ("informix", 9089, "sysmaster")
    assert db.dsn().startswith("DATABASE=sysmaster;HOSTNAME=ifx1;PORT=9089;")


def test_unknown_engine():
    raw = {"databases": [dict(BASE["databases"][0], engine="oracle")]}
    with pytest.raises(ConfigError, match="unknown engine 'oracle'"):
        parse_config(raw, env={"PW": "x"})


def test_same_name_on_two_engines_is_allowed():
    raw = {"databases": [BASE["databases"][0], dict(BASE["databases"][0], engine="informix")]}
    assert len(parse_config(raw, env={"PW": "x"}).databases) == 2


def test_select_engines_before_reading_passwords():
    raw = {"databases": [BASE["databases"][0],
                         {"name": "ifx", "engine": "informix", "host": "i", "user": "u", "password_env": "IFX_PW"}]}
    # IFX_PW is not set, which is fine when only Db2 is selected
    assert [d.engine for d in parse_config(raw, env={"PW": "x"}, engines="db2").databases] == ["db2"]
    assert len(parse_config(raw, env={"PW": "x", "IFX_PW": "y"}, engines="").databases) == 2
    with pytest.raises(ConfigError, match="unknown engine"):
        parse_config(raw, env={"PW": "x"}, engines="mysql")
    with pytest.raises(ConfigError, match="no targets left"):
        parse_config({"databases": BASE["databases"]}, env={"PW": "x"}, engines="informix")
