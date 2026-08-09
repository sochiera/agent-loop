from __future__ import annotations

import os

import pytest

from forge import preflight, provider_env
from forge.agents import AgentError


CONFIG = {"provider": {
    "chmura": {"options": {"baseURL": "https://x/v1",
                           "apiKey": "{env:CHMURA_KEY}"}},
    "z-naglowkiem": {"options": {"headers": {"X-Auth": "{env:INNY_KEY}"}}},
    "lokalny": {"options": {"apiKey": "local"}},
}}


def test_wymagania_obejmuja_klucze_zagniezdzone_w_naglowkach() -> None:
    # {env:...} bywa nie tylko w apiKey — skan płytki przepuściłby providera
    # autoryzowanego nagłówkiem, czyli dokładnie ten, który padnie na 401.
    assert provider_env.provider_vars(CONFIG) == {
        "chmura": {"CHMURA_KEY"}, "z-naglowkiem": {"INNY_KEY"}}


def test_provider_bez_podstawien_nie_jest_wymaganiem() -> None:
    assert "lokalny" not in provider_env.provider_vars(CONFIG)


def test_brakuje_tylko_tego_czego_wymagaja_uzyte_modele() -> None:
    # Klucz do dostawcy, do którego ten przebieg nie zadzwoni, nie jest brakiem.
    braki = provider_env.missing(["chmura/duzy"], CONFIG, {})

    assert braki == [("chmura", "CHMURA_KEY")]


def test_model_bez_prefiksu_providera_nie_wnosi_wymagan() -> None:
    assert provider_env.missing(["sonnet"], CONFIG, {}) == []


def test_pusta_zmienna_liczy_sie_jako_brak() -> None:
    # OpenCode podstawi pusty string i wyśle żądanie bez klucza — dla nas to
    # nie jest „ustawione".
    braki = provider_env.missing(["chmura/duzy"], CONFIG, {"CHMURA_KEY": "  "})

    assert braki == [("chmura", "CHMURA_KEY")]


class TestPlikiEnv:
    def test_czyta_export_cudzyslowy_i_pomija_komentarze(self, tmp_path) -> None:
        plik = tmp_path / "dostawca.env"
        plik.write_text(
            "# komentarz\n"
            "export A=jeden\n"
            "B='dwa'\n"
            '  C = "trzy"  \n'
            "\n"
            "nie-przypisanie\n", encoding="utf-8")

        assert provider_env.parse_env_file(plik) == {
            "A": "jeden", "B": "dwa", "C": "trzy"}

    def test_pomija_wartosci_wymagajace_shella(self, tmp_path) -> None:
        # `A=$INNA` bez shella nie ma wartości; wstawienie literału podłożyłoby
        # do żądania śmieć zamiast klucza i dałoby mylące 401.
        plik = tmp_path / "x.env"
        plik.write_text("A=$INNA\nB=`cat /k`\nC=dobre\n", encoding="utf-8")

        assert provider_env.parse_env_file(plik) == {"C": "dobre"}

    def test_nieczytelny_plik_nie_wywraca_preflightu(self, tmp_path) -> None:
        assert provider_env.parse_env_file(tmp_path / "nie-ma.env") == {}

    def test_lista_plikow_z_konfiguracji_zastepuje_szukanie(
            self, tmp_path) -> None:
        pierwszy, drugi = tmp_path / "a.env", tmp_path / "b.env"
        environ = {"FORGE_ENV_FILES": os.pathsep.join(
            [str(pierwszy), str(drugi)])}

        assert provider_env.env_files(environ) == [pierwszy, drugi]

    def test_wylaczenie_dobierania(self) -> None:
        assert provider_env.env_files({"FORGE_ENV_FILES": "none"}) == []

    def test_szuka_obok_pliku_konfiguracyjnego_opencode(self, tmp_path) -> None:
        (tmp_path / "klucze.env").write_text("A=1\n", encoding="utf-8")
        environ = {"OPENCODE_CONFIG": str(tmp_path / "opencode.json")}

        assert provider_env.env_files(environ) == [tmp_path / "klucze.env"]


class TestDobieranieKluczy:
    def test_uzupelnia_brakujacy_klucz_z_pliku(self, tmp_path) -> None:
        (tmp_path / "chmura.env").write_text(
            "export CHMURA_KEY=sekret\n", encoding="utf-8")
        environ = {"FORGE_ENV_FILES": str(tmp_path / "chmura.env")}

        dobrane, braki = provider_env.resolve(["chmura/duzy"], CONFIG, environ)

        assert (dobrane, braki) == (["CHMURA_KEY"], [])
        assert environ["CHMURA_KEY"] == "sekret"

    def test_jawne_srodowisko_wygrywa_z_plikiem(self, tmp_path) -> None:
        # Inaczej `CHMURA_KEY=... forge ...` byłoby po cichu ignorowane.
        (tmp_path / "chmura.env").write_text(
            "CHMURA_KEY=z-pliku\n", encoding="utf-8")
        environ = {"FORGE_ENV_FILES": str(tmp_path / "chmura.env"),
                   "CHMURA_KEY": "z-wiersza-polecen"}

        dobrane, _braki = provider_env.resolve(["chmura/duzy"], CONFIG, environ)

        assert dobrane == []
        assert environ["CHMURA_KEY"] == "z-wiersza-polecen"

    def test_nie_dobiera_kluczy_nieuzywanych_providerow(self, tmp_path) -> None:
        # Plik z kluczami wszystkich dostawców nie może wsypywać do środowiska
        # sekretów, których ten przebieg nie potrzebuje.
        (tmp_path / "wszystko.env").write_text(
            "CHMURA_KEY=a\nINNY_KEY=b\n", encoding="utf-8")
        environ = {"FORGE_ENV_FILES": str(tmp_path / "wszystko.env")}

        provider_env.resolve(["chmura/duzy"], CONFIG, environ)

        assert "INNY_KEY" not in environ


class TestPreflight:
    def _cfg(self, models: list[str], blocked: list[str] | None = None):
        class Cfg:
            def opencode_models_in_use(self) -> list[str]:
                return models

            def roles_blocked_by(self, providers: set[str]) -> list[str]:
                return list(blocked or [])
        return Cfg()

    def test_przerywa_z_nazwa_brakujacej_zmiennej(
            self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(preflight, "opencode_user_config", lambda: CONFIG)
        monkeypatch.setenv("FORGE_ENV_FILES", "none")
        monkeypatch.delenv("CHMURA_KEY", raising=False)

        with pytest.raises(AgentError) as błąd:
            preflight.ensure_provider_credentials(
                str(tmp_path), self._cfg(["chmura/duzy"], ["tester/complex"]))

        assert "CHMURA_KEY" in str(błąd.value)
        assert "tester/complex" in str(błąd.value)

    def test_brak_klucza_z_dzialajacym_zapasem_tylko_ostrzega(
            self, tmp_path, monkeypatch) -> None:
        # Rola, która ma czym wykonać zadanie, nie może blokować startu — koszt
        # niepotrzebnego zatrzymania jest wyższy niż utrata jednego dostawcy.
        monkeypatch.setattr(preflight, "opencode_user_config", lambda: CONFIG)
        monkeypatch.setenv("FORGE_ENV_FILES", "none")
        monkeypatch.delenv("CHMURA_KEY", raising=False)

        assert preflight.ensure_provider_credentials(
            str(tmp_path), self._cfg(["chmura/duzy"], [])) == []

    def test_przechodzi_gdy_klucz_dobrany_z_pliku(
            self, tmp_path, monkeypatch) -> None:
        plik = tmp_path / "chmura.env"
        plik.write_text("CHMURA_KEY=sekret\n", encoding="utf-8")
        monkeypatch.setattr(preflight, "opencode_user_config", lambda: CONFIG)
        monkeypatch.setenv("FORGE_ENV_FILES", str(plik))
        monkeypatch.delenv("CHMURA_KEY", raising=False)

        dobrane = preflight.ensure_provider_credentials(
            str(tmp_path), self._cfg(["chmura/duzy"]))

        assert dobrane == ["CHMURA_KEY"]
        assert os.environ["CHMURA_KEY"] == "sekret"

    def test_bez_opencode_w_routingu_nic_nie_sprawdza(
            self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(preflight, "opencode_user_config", lambda: CONFIG)

        assert preflight.ensure_provider_credentials(
            str(tmp_path), self._cfg([])) == []
