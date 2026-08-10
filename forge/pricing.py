"""Cennik API per (agent, model) — jedyna nowa DANA metryki ``$/zadanie``.

Wycena jest jawnie czterowymiarowa: ``(wejście nieocache'owane, zapis cache,
odczyt cache, wyjście)``. Trójka „wejście / z cache / wyjście" tu nie wystarcza,
bo zapis do cache'u kosztuje 1,25× wejścia i jest dokładnie tą pozycją, przez
którą rola wołana co rundę potrafi przepłacać (patrz ``master_gate``).

Zasada nadrzędna: **brak stawki to ``None``, nigdy 0**. Cicha wycena na zero
udawałaby, że model jest darmowy, i przekłamywałaby ``$/zadanie`` w dół —
czyli psułaby dokładnie tę liczbę, dla której ten moduł powstał.
"""
from __future__ import annotations

import os

MILLION = 1_000_000

# Stawka = (wejście, zapis cache, odczyt cache, wyjście) w USD za milion tokenów.
Rates = tuple[float, float, float, float]

# Sonnet ma promocję $2/$10 do 2026-08-31. Świadomie jej NIE wpisujemy: stawka
# promocyjna wygasa w połowie okresu pomiarowego, więc porównanie dwóch
# przebiegów przestałoby być uczciwe. Zawyżenie kosztu jest tu bezpieczniejsze
# niż jego zaniżenie.
CLAUDE_RATES: dict[str, Rates] = {
    #            in     cache_write(1,25×)  cache_read(0,1×)   out
    "opus":    (5.00,   6.25,               0.50,              25.00),
    "sonnet":  (3.00,   3.75,               0.30,              15.00),
    "haiku":   (1.00,   1.25,               0.10,               5.00),
}

# Codex: proporcje znamy, kotwicy w dolarach nie. Mnożnik jest względem Sola.
CODEX_MULTIPLIERS: dict[str, float] = {
    "gpt-5.6-sol":   1.00,
    "gpt-5.6-terra": 0.40,   # Sol / 2,5
    "gpt-5.6-luna":  0.04,   # Terra / 10
}
# Odczyt z cache'u u tego providera jest ułamkiem stawki wejścia; zapisu nie
# rozlicza osobno (patrz ``report._tokens`` — dla Codeksa zapis jest zawsze 0).
CODEX_CACHE_READ_FACTOR = 0.1

# Model lokalny: kosztem jest prąd, nie API. Zero jest tu FAKTEM, nie brakiem
# danych, więc jako jedyne wolno mu wejść do sumy.
LOCAL_PREFIXES = ("llamacpp/",)

# Cennik KATALOGOWY API (models.dev) dla modeli wołanych przez opencode,
# kluczowany pełnym ``provider/model``.
#
# To ŚWIADOMY rozdźwięk z fakturą: Luna chodzi na OAuth konta, a GLM-5.2 na
# abonamencie Coding Plan, więc realnie te tokeny kosztują mniej albo nic.
# Wyceniamy je mimo to stawką „jak po API", bo ``$/zadanie`` ma mierzyć pracę
# modelu i pozwalać porównywać przebiegi między providerami — a nie to, który
# abonament akurat mamy wykupiony. Liczba jest więc górnym ograniczeniem
# kosztu, nie rachunkiem do zapłaty.
#
# Dwa zastrzeżenia do samych liczb:
# * Luna ma próg kontekstu — powyżej 272k tokenów wszystkie cztery stawki
#   podwajają się (0,40 / 0,50 / 0,04 / 1,80). Czwórka stawek nie ma wymiaru
#   „rozmiar kontekstu", więc trzymamy próg dolny i świadomie zaniżamy wycenę
#   najdłuższych sesji.
# * Zera w kolumnie zapisu cache'u dla GLM to FAKT z cennika z.ai (ten
#   provider nie rozlicza zapisu osobno), a nie brak danych.
API_LIST_RATES: dict[str, Rates] = {
    #                            in    cache_write  cache_read    out
    "openai/gpt-5.6-luna":     (0.20,  0.25,        0.02,        1.20),
    "zai-coding-plan/glm-5.2": (1.40,  0.00,        0.26,        4.40),
    "zai/glm-5.2":             (1.40,  0.00,        0.26,        4.40),
    "zhipuai/glm-5.2":         (1.40,  0.00,        0.26,        4.40),
}


def _codex_anchor() -> tuple[float, float]:
    """Kotwica cenowa Sola (wejście, wyjście) w USD/mln; 0 = brak wyceny.

    Czytane przy każdym wywołaniu, a nie przy imporcie: raport bywa uruchamiany
    z tego samego procesu co pętla, a kotwicę ustawia się z zewnątrz.
    """
    def _read(name: str) -> float:
        try:
            return float(os.environ.get(name, "0") or 0)
        except ValueError:
            return 0.0
    return _read("FORGE_PRICE_SOL_IN"), _read("FORGE_PRICE_SOL_OUT")


def rates(agent: str, model: str) -> Rates | None:
    """Stawki dla ``(agent, model)`` albo ``None``, gdy cennika nie znamy."""
    agent = (agent or "").strip().lower()
    model = (model or "").strip()
    if agent == "claude":
        return CLAUDE_RATES.get(model.lower())
    if agent in {"codex", "gpt"}:
        multiplier = CODEX_MULTIPLIERS.get(model.lower())
        anchor_in, anchor_out = _codex_anchor()
        # OBIE wartości muszą być dodatnie. Kotwica ustawiona w połowie dawała
        # stawkę 0 dla drugiej strony i cicho zaniżała rachunek — czyli robiła
        # dokładnie to, przed czym broni cała reguła „brak stawki to None".
        if multiplier is None or anchor_in <= 0 or anchor_out <= 0:
            return None
        rate_in = anchor_in * multiplier
        return (rate_in, rate_in, rate_in * CODEX_CACHE_READ_FACTOR,
                anchor_out * multiplier)
    if model.startswith(LOCAL_PREFIXES):
        return (0.0, 0.0, 0.0, 0.0)
    return API_LIST_RATES.get(model.lower())


def cost_usd(agent: str, model: str,
             tokens: tuple[int, int, int, int]) -> float | None:
    """Koszt czwórki ``(uncached_in, cache_write, cache_read, out)`` w USD.

    ``None`` oznacza „nie znam stawki" i musi zostać ``None`` aż do raportu —
    tam pokazuje się jako ``—`` razem z ostrzeżeniem.
    """
    rate = rates(agent, model)
    if rate is None:
        return None
    return sum(count * price for count, price in zip(tokens, rate)) / MILLION
