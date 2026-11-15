#!/usr/bin/env python3
"""trader-morning: resumen diario antes de la apertura. Solo librería estándar.

    python3 morning.py                       # analiza watchlist.txt, escribe docs/data.json
    python3 morning.py --send                # además envía el correo (SMTP_USER, SMTP_PASS, MAIL_TO)
    python3 morning.py --send --gate-ny 8    # solo si en Nueva York es la hora 8 (para cron en UTC)

Capas: datos (get, bars, sentiment) -> análisis (analyse) -> presentación (write_json, send_mail).
Datos gratuitos de Yahoo Finance: no oficiales, con retraso, pueden fallar. Solo largos. No envía órdenes.
"""
import argparse, json, os, re, smtplib, ssl, sys, time
import urllib.error, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.message import EmailMessage

UA = "Mozilla/5.0 (compatible; trader-morning/1.0)"
POS = set("beat beats surge surges soar soars jump jumps rally gain gains rise rises record upgrade "
          "upgraded growth strong profit profits bullish outperform raises raised wins approval".split())
NEG = set("miss misses fall falls drop drops plunge plunges slump cut cuts downgrade downgraded lawsuit "
          "probe weak loss losses bearish recall fraud warns warning layoffs decline declines halt".split())
NOTE = "Simulación educativa, no asesoramiento financiero. Datos gratuitos con retraso. Un stop no garantiza el precio de salida."


class DataError(Exception):
    pass


# ---------------------------------------------------------------- datos
def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and i < tries - 1:
                time.sleep(2 * (i + 1))
                continue
            raise DataError("HTTP %s en %s" % (e.code, url.split("?")[0]))
        except urllib.error.URLError as e:
            if i < tries - 1:
                time.sleep(2)
                continue
            raise DataError("sin conexión: %s" % e.reason)


def bars(sym):
    url = "https://query2.finance.yahoo.com/v8/finance/chart/%s?range=6mo&interval=1d" % sym.replace(".", "-")
    try:
        r = json.loads(get(url))["chart"]["result"][0]
        q = r["indicators"]["quote"][0]
    except (ValueError, KeyError, TypeError, IndexError):
        raise DataError("respuesta inesperada de Yahoo")
    rows = [(c, h, l) for c, h, l in zip(q["close"], q["high"], q["low"]) if None not in (c, h, l)]
    if len(rows) < 60:
        raise DataError("pocos datos (%d días)" % len(rows))
    return rows, r["meta"].get("regularMarketPrice")


def sentiment(sym):
    """Media de -1..1 por titular con un léxico simple (ingles). Es una señal débil."""
    root = ET.fromstring(get("https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=US&lang=en-US" % sym))
    heads, vals = [], []
    for it in list(root.iter("item"))[:10]:
        t = (it.findtext("title") or "").strip()
        w = re.findall(r"[a-z']+", t.lower())
        vals.append(max(-1, min(1, sum(x in POS for x in w) - sum(x in NEG for x in w))))
        heads.append(t)
    return (sum(vals) / len(vals) if vals else 0.0), heads[:3]


# ---------------------------------------------------------------- análisis
def sma(x, n):
    return sum(x[-n:]) / n


def rsi(c, n=14):
    g = [max(0, c[i] - c[i - 1]) for i in range(1, len(c))]
    l = [max(0, c[i - 1] - c[i]) for i in range(1, len(c))]
    ag, al = sum(g[:n]) / n, sum(l[:n]) / n
    for i in range(n, len(g)):
        ag, al = (ag * (n - 1) + g[i]) / n, (al * (n - 1) + l[i]) / n
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def atr(rows, n=14):
    tr = [max(h - l, abs(h - rows[i - 1][0]), abs(l - rows[i - 1][0])) for i, (c, h, l) in enumerate(rows) if i]
    return sum(tr[-n:]) / n


def analyse(sym, rows, px, sent, heads):
    c = [r[0] for r in rows]
    px = px or c[-1]
    s20, s50, r, a = sma(c, 20), sma(c, 50), rsi(c), atr(rows)
    sup, res = min(x[2] for x in rows[-20:]), max(x[1] for x in rows[-20:])
    score, why = 0, []
    score += 1 if px > s20 else -1
    why.append("precio %s su media de 20 días" % ("sobre" if px > s20 else "bajo"))
    score += 1 if s20 > s50 else -1
    why.append("tendencia %s (media 20 %s 50)" % ("alcista" if s20 > s50 else "bajista", ">" if s20 > s50 else "<"))
    if r < 30:
        score += 1
        why.append("RSI %.0f: sobrevendido" % r)
    elif r > 75:
        score -= 1
        why.append("RSI %.0f: sobrecomprado" % r)
    else:
        why.append("RSI %.0f" % r)
    if sent > 0.15:
        score += 1
        why.append("noticias positivas (%+.2f)" % sent)
    elif sent < -0.15:
        score -= 1
        why.append("noticias negativas (%+.2f)" % sent)
    out = {"ticker": sym, "price": round(px, 2), "score": score, "support": round(sup, 2),
           "resistance": round(res, 2), "why": why, "headlines": heads, "action": "Esperar",
           "entry": None, "stop": None, "target": None, "rr": None}
    if score >= 3 and r < 75:
        stop = round(px - 1.5 * a, 2)
        out.update(action="Comprar", entry=round(px, 2), stop=stop, target=round(px + 2 * (px - stop), 2), rr=2.0)
    elif score <= -2:
        out.update(action="Vender", entry=round(px, 2))  # salir si la tienes; no se abre corto
    return out


# ---------------------------------------------------------------- presentación
def write_json(path, doc):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)


def mail_text(doc):
    lines = ["Resumen pre-apertura, %s" % doc["generated"][:16].replace("T", " ") + " UTC", ""]
    acts = [p for p in doc["picks"] if p["action"] != "Esperar"]
    for p in acts:
        if p["action"] == "Comprar":
            lines.append("COMPRAR %s: entrada ~%.2f | stop %.2f | objetivo %.2f (R:R %.1f)" %
                         (p["ticker"], p["entry"], p["stop"], p["target"], p["rr"]))
        else:
            lines.append("VENDER %s (salir si la tienes): precio %.2f" % (p["ticker"], p["price"]))
        lines.append("   " + "; ".join(p["why"]))
    if not acts:
        lines.append("Hoy no hay señales claras: esperar.")
    lines += ["", "Sin señal: " + ", ".join(p["ticker"] for p in doc["picks"] if p["action"] == "Esperar")]
    if doc["errors"]:
        lines += ["", "Errores: " + "; ".join(doc["errors"])]
    return "\n".join(lines + ["", NOTE])


def send_mail(doc):
    user, pw, to = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS"), os.environ.get("MAIL_TO")
    if not (user and pw and to):
        raise DataError("faltan SMTP_USER, SMTP_PASS o MAIL_TO")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = "Resumen pre-apertura", user, to
    msg.set_content(mail_text(doc))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(user, pw)
        s.send_message(msg)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--watch", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.txt"))
    ap.add_argument("--out", default="docs/data.json")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--gate-ny", type=int, help="solo corre si en Nueva York es esta hora (0-23)")
    ap.add_argument("--force", action="store_true", help="ignora --gate-ny")
    a = ap.parse_args(argv)
    if a.gate_ny is not None and not a.force:
        from zoneinfo import ZoneInfo
        if datetime.now(ZoneInfo("America/New_York")).hour != a.gate_ny:
            print("fuera de hora: no hago nada")
            return 0
    with open(a.watch, encoding="utf-8") as fh:
        syms = [s.strip().upper() for s in fh if s.strip() and not s.startswith("#")]
    picks, errors = [], []
    for s in syms:
        try:
            rows, px = bars(s)
            try:
                sent, heads = sentiment(s)
            except (DataError, ET.ParseError) as e:
                sent, heads = 0.0, []
                errors.append("%s noticias: %s" % (s, e))
            picks.append(analyse(s, rows, px, sent, heads))
        except DataError as e:
            errors.append("%s: %s" % (s, e))
        time.sleep(0.5)
    if not picks:
        print("sin datos: " + "; ".join(errors), file=sys.stderr)
        return 1
    order = {"Comprar": 0, "Vender": 1, "Esperar": 2}
    picks.sort(key=lambda p: (order[p["action"]], -abs(p["score"])))
    doc = {"generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
           "source": "Yahoo Finance (gratuito, con retraso)", "picks": picks, "errors": errors, "note": NOTE}
    write_json(a.out, doc)
    print(mail_text(doc))
    if a.send:
        try:
            send_mail(doc)
            print("correo enviado")
        except (DataError, smtplib.SMTPException, OSError) as e:
            print("correo NO enviado: %s" % e, file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
