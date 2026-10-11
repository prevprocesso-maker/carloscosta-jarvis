"""Previsao do tempo FALADA (01/10/2026).

Ate esta data a ferramenta so abria uma busca do Google no navegador e
respondia, em ingles, "Showing the weather for Curitiba, today, sir." — o dono
perguntava "vai chover amanha?" e ouvia uma frase em ingles sem a previsao.
Sem cidade, a resposta era "Sir, the city is missing", tambem em ingles, mesmo
com a regra do cerebro mandando "chame assim mesmo, o PC usa a cidade
cadastrada" (cidade cadastrada que ninguem lia).

Agora a previsao vem do Open-Meteo (gratis, sem chave, ~0,3 s) e sai pronta
para falar: temperatura, ceu e chance de chuva, para hoje, amanha, um dia da
semana ou a semana inteira. Sem cidade: a da memoria do dono; depois a da
conexao de internet. So se a rede inteira falhar ele volta a abrir o navegador.
"""
import json
import time
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote_plus

_RAIZ = Path(__file__).resolve().parents[1]
_TIMEOUT = 6

# Codigo do tempo (WMO) -> como se fala.
_CEU_PT = {
    0: "céu limpo", 1: "quase sem nuvens", 2: "parcialmente nublado", 3: "nublado",
    45: "neblina", 48: "neblina", 51: "garoa fraca", 53: "garoa", 55: "garoa forte",
    56: "garoa gelada", 57: "garoa gelada", 61: "chuva fraca", 63: "chuva",
    65: "chuva forte", 66: "chuva gelada", 67: "chuva gelada", 71: "neve fraca",
    73: "neve", 75: "neve forte", 77: "neve", 80: "pancadas de chuva fracas",
    81: "pancadas de chuva", 82: "pancadas de chuva fortes", 85: "neve", 86: "neve forte",
    95: "trovoadas", 96: "trovoadas com granizo", 99: "trovoadas com granizo",
}
_CEU_EN = {
    0: "clear skies", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle", 61: "light rain", 63: "rain",
    65: "heavy rain", 66: "freezing rain", 67: "freezing rain", 71: "light snow",
    73: "snow", 75: "heavy snow", 77: "snow", 80: "light showers", 81: "showers",
    82: "heavy showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail",
}
_DIAS_PT = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
_DIAS_EN = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _em_ingles() -> bool:
    try:
        from core.idioma import em_ingles  # noqa: PLC0415
        return bool(em_ingles())
    except Exception:
        return False


def _sem_acento(t: str) -> str:
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _baixar(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "JARVIS-CyberBot"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _cidade_da_memoria() -> str:
    try:
        dados = json.loads((_RAIZ / "memory" / "long_term.json").read_text(encoding="utf-8"))
        for chave in ("city", "cidade", "hometown", "location"):
            v = ((dados.get("identity") or {}).get(chave) or {})
            v = v.get("value") if isinstance(v, dict) else v
            if v:
                return str(v).strip()
    except Exception:
        pass
    return ""


_CIDADE_IP = {"valor": "", "quando": 0.0}


def _cidade_da_internet() -> str:
    """A cidade da conexao do PC. Guardada por 6 h: ela nao muda a toda hora."""
    if _CIDADE_IP["valor"] and time.time() - _CIDADE_IP["quando"] < 6 * 3600:
        return _CIDADE_IP["valor"]
    for url, campo in (("https://ipapi.co/json/", "city"), ("https://ipinfo.io/json", "city")):
        try:
            v = str(_baixar(url).get(campo) or "").strip()
            if v:
                _CIDADE_IP.update(valor=v, quando=time.time())
                return v
        except Exception:
            continue
    return ""


def _onde(cidade: str) -> tuple:
    """(latitude, longitude, nome falado) da cidade pedida."""
    nome = cidade.split(",")[0].strip()
    lingua = "en" if _em_ingles() else "pt"
    url = ("https://geocoding-api.open-meteo.com/v1/search?count=5&format=json&language="
           + lingua + "&name=" + urllib.parse.quote(nome))
    achados = _baixar(url).get("results") or []
    if not achados:
        raise LookupError(cidade)
    # Com estado/pais no pedido ("Antonina, Parana"), prefere o que bate.
    resto = _sem_acento(cidade[len(nome):])
    melhor = achados[0]
    if resto.strip(" ,"):
        for a in achados:
            junto = _sem_acento(f"{a.get('admin1', '')} {a.get('country', '')}")
            if any(p and p in junto for p in resto.replace(",", " ").split()):
                melhor = a
                break
    return melhor["latitude"], melhor["longitude"], melhor.get("name") or nome


def _qual_dia(quando: str, hoje: date) -> int | None:
    """Indice do dia pedido (0 = hoje). None = a semana inteira."""
    q = _sem_acento(quando)
    if any(p in q for p in ("semana", "week", "proximos dias", "next days")):
        return None
    if "depois de amanha" in q or "day after tomorrow" in q:
        return 2
    if "amanha" in q or "tomorrow" in q:
        return 1
    for lista in (_DIAS_PT, _DIAS_EN):
        for i, nome in enumerate(lista):
            if _sem_acento(nome) in q:
                return (i - hoje.weekday()) % 7 or 0
    return 0


def _frase_do_dia(d: dict, i: int, en: bool) -> str:
    ceu = (_CEU_EN if en else _CEU_PT).get(int(d["weather_code"][i]), "")
    tmin = round(d["temperature_2m_min"][i])
    tmax = round(d["temperature_2m_max"][i])
    chuva = d.get("precipitation_probability_max", [None] * 7)[i]
    if en:
        s = f"{ceu}, from {tmin} to {tmax} degrees"
        if chuva is not None:
            s += f", {chuva}% chance of rain"
    else:
        s = f"{ceu}, de {tmin} a {tmax} graus"
        if chuva is not None:
            s += f", {chuva}% de chance de chuva"
    return s


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    en = _em_ingles()
    cidade = str(params.get("city") or "").strip()
    quando = str(params.get("time") or params.get("when") or params.get("day") or "hoje").strip()
    if not cidade:
        cidade = _cidade_da_memoria() or _cidade_da_internet()
    if not cidade:
        msg = "Which city, sir?" if en else "Para qual cidade, senhor?"
        _log(msg, player)
        return msg
    try:
        lat, lon, nome = _onde(cidade)
        dados = _baixar(
            "https://api.open-meteo.com/v1/forecast?timezone=auto&forecast_days=7"
            f"&latitude={lat}&longitude={lon}"
            "&current=temperature_2m,weather_code"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max")
        d = dados["daily"]
        hoje = date.fromisoformat(d["time"][0])
        i = _qual_dia(quando, hoje)
        if i is None:
            partes = []
            for k in range(min(5, len(d["time"]))):
                dia = date.fromisoformat(d["time"][k])
                rotulo = (("today" if en else "hoje") if k == 0 else
                          ("tomorrow" if en else "amanhã") if k == 1 else
                          (_DIAS_EN if en else _DIAS_PT)[dia.weekday()])
                partes.append(f"{rotulo}: {_frase_do_dia(d, k, en)}")
            msg = (f"Next days in {nome}: " if en else f"Próximos dias em {nome}: ") + "; ".join(partes) + "."
        else:
            i = min(i, len(d["time"]) - 1)
            dia = hoje + timedelta(days=i)
            rotulo = (("Today" if en else "Hoje") if i == 0 else
                      ("Tomorrow" if en else "Amanhã") if i == 1 else
                      ("On " + _DIAS_EN[dia.weekday()].capitalize() if en
                       else "Na " + _DIAS_PT[dia.weekday()] if dia.weekday() < 5
                       else "No " + _DIAS_PT[dia.weekday()]))
            msg = f"{rotulo} {'in' if en else 'em'} {nome}: {_frase_do_dia(d, i, en)}."
            agora = (dados.get("current") or {}).get("temperature_2m")
            if i == 0 and agora is not None:
                msg += (f" Right now it's {round(agora)} degrees." if en
                        else f" Agora faz {round(agora)} graus.")
        _log(msg, player)
        if session_memory:
            try:
                session_memory.set_last_search(query=f"tempo {cidade} {quando}", response=msg)
            except Exception:
                pass
        return msg
    except LookupError:
        msg = (f"I couldn't find the city {cidade}, sir." if en
               else f"Não achei a cidade {cidade}, senhor.")
        _log(msg, player)
        return msg
    except Exception as exc:
        # Sem rede para a previsao: o jeito antigo, mas dito na lingua dele.
        _log(f"previsao falhou: {exc}", player)
        try:
            webbrowser.open("https://www.google.com/search?q="
                            + quote_plus(f"previsao do tempo {cidade} {quando}"))
            return (f"I couldn't get the forecast now, so I opened it in the browser for {cidade}."
                    if en else
                    f"Não consegui buscar a previsão agora; abri o tempo de {cidade} no navegador.")
        except Exception:
            return ("I couldn't get the weather right now, sir." if en
                    else "Não consegui ver a previsão do tempo agora, senhor.")


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass
