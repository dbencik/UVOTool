#!/usr/bin/env python3
"""
vestnik-profile.py — Investigative profiling tool for Slovak public procurement.

Generates comprehensive, fact-based reports about suppliers (dodávatelia)
or buyers (obstarávatelia) from the UVO Vestník database.

Usage:
    python3 tools/vestnik-profile.py --dodavatel 36019208
    python3 tools/vestnik-profile.py --dodavatel "INMEDIA"
    python3 tools/vestnik-profile.py --obstaravatel 36038351
    python3 tools/vestnik-profile.py --obstaravatel "LESY"
"""

import argparse
import os
import sqlite3
import sys
import statistics
from collections import defaultdict
from pathlib import Path

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'vestnik.db')
REPORTS_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'reports')


def fmt_eur(value):
    """Format number as Slovak EUR: 1 234 567 EUR"""
    if value is None:
        return "N/A"
    if value == int(value):
        s = f"{int(value):,}".replace(",", " ")
    else:
        s = f"{value:,.2f}".replace(",", " ")
    return f"{s} EUR"


def fmt_num(value):
    """Format number with Slovak thousands separator."""
    if value is None:
        return "N/A"
    if isinstance(value, float):
        if value == int(value):
            return f"{int(value):,}".replace(",", " ")
        return f"{value:,.2f}".replace(",", " ")
    return f"{value:,}".replace(",", " ")


def fmt_pct(value):
    """Format percentage."""
    if value is None:
        return "N/A"
    return f"{value:.1f}%"


def zakazka_word(n):
    """Slovak declension for zákazka: 1 zákazku, 2-4 zákazky, 5+ zákaziek."""
    if n == 1:
        return "1 zákazku"
    elif 2 <= n <= 4:
        return f"{fmt_num(n)} zákazky"
    else:
        return f"{fmt_num(n)} zákaziek"


def pripad_word(n):
    """Slovak declension for prípad: 1 prípade, 2-4 prípadoch, 5+ prípadoch."""
    if n == 1:
        return "1 prípade"
    else:
        return f"{fmt_num(n)} prípadoch"


def connect_db():
    if not os.path.exists(DB_PATH):
        print(f"CHYBA: Databáza nenájdená: {DB_PATH}")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def resolve_dodavatel(conn, query):
    """Resolve dodávateľ by IČO or fuzzy name. Returns (nazov, ico, rows)."""
    # Try as IČO first
    rows = conn.execute(
        "SELECT DISTINCT nazov, ico FROM ucastnici WHERE ico = ?", (query,)
    ).fetchall()
    if rows:
        # Merge names — pick most common
        names = [r['nazov'] for r in rows if r['nazov']]
        nazov = max(set(names), key=names.count) if names else query
        return nazov, query

    # Also check zmeny_zmluv
    rows = conn.execute(
        "SELECT DISTINCT dodavatel_nazov, dodavatel_ico FROM zmeny_zmluv WHERE dodavatel_ico = ?",
        (query,)
    ).fetchall()
    if rows:
        names = [r['dodavatel_nazov'] for r in rows if r['dodavatel_nazov']]
        nazov = max(set(names), key=names.count) if names else query
        return nazov, query

    # Fuzzy name search
    rows = conn.execute(
        "SELECT DISTINCT nazov, ico FROM ucastnici WHERE UPPER(nazov) LIKE UPPER(?)",
        (f"%{query}%",)
    ).fetchall()
    if rows:
        # Pick the most frequent ICO
        icos = [r['ico'] for r in rows if r['ico']]
        if icos:
            ico = max(set(icos), key=icos.count)
            names = [r['nazov'] for r in rows if r['ico'] == ico]
            nazov = max(set(names), key=names.count) if names else query
            return nazov, ico

    return None, None


def resolve_obstaravatel(conn, query):
    """Resolve obstarávateľ by IČO or fuzzy name."""
    rows = conn.execute(
        "SELECT DISTINCT nazov, ico FROM obstaravatelia WHERE ico = ?", (query,)
    ).fetchall()
    if rows:
        names = [r['nazov'] for r in rows if r['nazov']]
        nazov = max(set(names), key=names.count) if names else query
        return nazov, query

    # Fuzzy name search
    rows = conn.execute(
        "SELECT DISTINCT nazov, ico FROM obstaravatelia WHERE UPPER(nazov) LIKE UPPER(?)",
        (f"%{query}%",)
    ).fetchall()
    if rows:
        icos = [r['ico'] for r in rows if r['ico']]
        if icos:
            ico = max(set(icos), key=icos.count)
            names = [r['nazov'] for r in rows if r['ico'] == ico]
            nazov = max(set(names), key=names.count) if names else query
            return nazov, ico

    return None, None


# ============================================================
#  DODÁVATEĽ PROFILE
# ============================================================

def profile_dodavatel(conn, nazov, ico):
    lines = []
    lines.append("=" * 72)
    lines.append(f"  PROFIL DODÁVATEĽA: {nazov}")
    lines.append(f"  IČO: {ico}")
    lines.append("=" * 72)
    lines.append("")

    # ---- 1. IDENTIFIKÁCIA ----
    lines.append("1. IDENTIFIKÁCIA")
    lines.append("-" * 40)
    info = conn.execute(
        "SELECT nazov, ico, velkost_podniku FROM ucastnici WHERE ico = ? AND nazov IS NOT NULL",
        (ico,)
    ).fetchall()
    sizes = [r['velkost_podniku'] for r in info if r['velkost_podniku']]
    size = max(set(sizes), key=sizes.count) if sizes else "neuvedená"
    lines.append(
        f"{nazov}, IČO {ico}. Veľkosť podniku: {size}."
    )
    lines.append("")

    # ---- 2. CELKOVÝ PROFIL ----
    lines.append("2. CELKOVÝ PROFIL DODÁVATEĽA")
    lines.append("-" * 40)

    wins = conn.execute("""
        SELECT u.doc_id, u.cena, u.nazov as u_nazov, d.rok,
               z.predmet, v.celkova_hodnota, v.mena
        FROM ucastnici u
        JOIN dokumenty d ON u.doc_id = d.id
        LEFT JOIN zakazky z ON u.doc_id = z.doc_id
        LEFT JOIN vysledky v ON u.doc_id = v.doc_id
        WHERE u.ico = ? AND u.je_vitaz = 1
        ORDER BY d.rok
    """, (ico,)).fetchall()

    all_participations = conn.execute("""
        SELECT u.doc_id, u.cena, d.rok
        FROM ucastnici u
        JOIN dokumenty d ON u.doc_id = d.id
        WHERE u.ico = ?
    """, (ico,)).fetchall()

    win_values = [r['cena'] for r in wins if r['cena'] and r['cena'] > 0]
    win_years = sorted(set(r['rok'] for r in wins if r['rok']))

    if not wins:
        lines.append("V databáze nie sú evidované žiadne víťazné zákazky pre tohto dodávateľa.")
        lines.append("")
    else:
        total_val = sum(win_values)
        avg_val = statistics.mean(win_values) if win_values else 0
        median_val = statistics.median(win_values) if win_values else 0
        min_val = min(win_values) if win_values else 0
        max_val = max(win_values) if win_values else 0
        year_from = min(win_years) if win_years else "N/A"
        year_to = max(win_years) if win_years else "N/A"

        # Find largest contract subject
        max_contract = max(wins, key=lambda r: r['cena'] if r['cena'] else 0)
        max_subject = max_contract['predmet'] or "neuvedený predmet"

        lines.append(
            f"Firma {nazov} získala od roku {year_from} do roku {year_to} celkovo "
            f"{fmt_num(len(wins))} zákaziek v hodnote {fmt_eur(total_val)}. "
            f"Priemerná zákazka mala hodnotu {fmt_eur(avg_val)}, "
            f"medián {fmt_eur(median_val)}. "
            f"Najmenšia zákazka bola {fmt_eur(min_val)}, "
            f"najväčšia zákazka bola {fmt_eur(max_val)} "
            f"za \"{max_subject}\"."
        )
        lines.append("")

    # ---- 3. ZÁKAZNÍCKY MIX ----
    lines.append("3. ZÁKAZNÍCKY MIX")
    lines.append("-" * 40)

    buyers = conn.execute("""
        SELECT o.nazov, o.ico, COUNT(*) as cnt, SUM(u.cena) as total
        FROM ucastnici u
        JOIN obstaravatelia o ON u.doc_id = o.doc_id
        WHERE u.ico = ? AND u.je_vitaz = 1
        GROUP BY o.ico
        ORDER BY total DESC
    """, (ico,)).fetchall()

    if buyers:
        total_revenue = sum(b['total'] for b in buyers if b['total'])
        for i, b in enumerate(buyers[:5]):
            pct = (b['total'] / total_revenue * 100) if total_revenue and b['total'] else 0
            if i == 0:
                lines.append(
                    f"Najvýznamnejším zákazníkom firmy {nazov} je {b['nazov']} "
                    f"(IČO: {b['ico']}) s {fmt_num(b['cnt'])} zákazkami "
                    f"v hodnote {fmt_eur(b['total'])}, "
                    f"čo predstavuje {fmt_pct(pct)} celkového obratu z verejného obstarávania."
                )
            else:
                lines.append(
                    f"Ďalším zákazníkom je {b['nazov']} ({fmt_num(b['cnt'])} zákaziek, "
                    f"{fmt_eur(b['total'])}, {fmt_pct(pct)})."
                )

        # Concentration warning
        if buyers and total_revenue:
            top_pct = (buyers[0]['total'] / total_revenue * 100) if buyers[0]['total'] else 0
            if top_pct > 50:
                lines.append(
                    f"\nUPOZORNENIE: Jeden zákazník ({buyers[0]['nazov']}) tvorí "
                    f"{fmt_pct(top_pct)} celkového obratu, čo predstavuje vysokú koncentráciu."
                )
    else:
        lines.append("Žiadni obstarávatelia neboli identifikovaní v spojení s týmto dodávateľom.")
    lines.append("")

    # ---- 4. SEKTOROVÉ ZAMERANIE ----
    lines.append("4. SEKTOROVÉ ZAMERANIE")
    lines.append("-" * 40)

    cpv_data = conn.execute("""
        SELECT z.cpv_kod, z.druh, COUNT(*) as cnt
        FROM ucastnici u
        JOIN zakazky z ON u.doc_id = z.doc_id
        WHERE u.ico = ? AND u.je_vitaz = 1 AND z.cpv_kod IS NOT NULL
        GROUP BY z.cpv_kod
        ORDER BY cnt DESC
    """, (ico,)).fetchall()

    druh_data = conn.execute("""
        SELECT z.druh, COUNT(*) as cnt
        FROM ucastnici u
        JOIN zakazky z ON u.doc_id = z.doc_id
        WHERE u.ico = ? AND u.je_vitaz = 1 AND z.druh IS NOT NULL AND z.druh != ''
        GROUP BY z.druh
        ORDER BY cnt DESC
    """, (ico,)).fetchall()

    total_cpv = sum(c['cnt'] for c in cpv_data)
    if cpv_data:
        top = cpv_data[0]
        lines.append(
            f"Firma sa primárne zameriava na oblasť CPV {top['cpv_kod']}, "
            f"kde získala {fmt_num(top['cnt'])} z {fmt_num(total_cpv)} zákaziek."
        )
        for c in cpv_data[1:3]:
            lines.append(
                f"Ďalšou oblasťou je CPV {c['cpv_kod']} ({fmt_num(c['cnt'])} zákaziek)."
            )
    else:
        lines.append("CPV kódy neboli identifikované.")

    if druh_data:
        druh_list = ", ".join(f"{d['druh']} ({fmt_num(d['cnt'])})" for d in druh_data[:3])
        lines.append(f"Druhy zákaziek: {druh_list}.")
    lines.append("")

    # ---- 5. SÚŤAŽNÉ SPRÁVANIE ----
    lines.append("5. SÚŤAŽNÉ SPRÁVANIE")
    lines.append("-" * 40)

    total_bids = len(all_participations)
    total_wins_count = len(wins)
    win_rate = (total_wins_count / total_bids * 100) if total_bids else 0

    # Single-bidder rate
    single_bidder = conn.execute("""
        SELECT COUNT(*) as cnt FROM (
            SELECT u.doc_id
            FROM ucastnici u
            JOIN vysledky v ON u.doc_id = v.doc_id
            WHERE u.ico = ? AND u.je_vitaz = 1 AND v.pocet_ponuk = 1
        )
    """, (ico,)).fetchone()['cnt']

    # Average competitors
    avg_competitors = conn.execute("""
        SELECT AVG(v.pocet_ponuk) as avg_ponuk
        FROM ucastnici u
        JOIN vysledky v ON u.doc_id = v.doc_id
        WHERE u.ico = ? AND v.pocet_ponuk IS NOT NULL
    """, (ico,)).fetchone()['avg_ponuk']

    lines.append(
        f"Z {fmt_num(total_bids)} tendrov, v ktorých firma súťažila, "
        f"zvíťazila v {fmt_num(total_wins_count)} prípadoch "
        f"(win rate {fmt_pct(win_rate)})."
    )
    single_pct = (single_bidder / total_wins_count * 100) if total_wins_count else 0
    lines.append(
        f"V {fmt_num(single_bidder)} prípadoch bola jediným uchádzačom "
        f"({fmt_pct(single_pct)})."
    )
    if avg_competitors:
        lines.append(
            f"Priemerný počet ponúk v tendroch, kde firma súťažila, "
            f"bol {fmt_num(round(avg_competitors, 1))}."
        )
    lines.append("")

    # ---- 6. CENOVÝ PROFIL ----
    lines.append("6. CENOVÝ PROFIL")
    lines.append("-" * 40)

    price_ratios = conn.execute("""
        SELECT u.cena, p.hodnota, d.rok
        FROM ucastnici u
        JOIN prilezitosti p ON u.doc_id = p.doc_id
        JOIN dokumenty d ON u.doc_id = d.id
        WHERE u.ico = ? AND u.je_vitaz = 1
              AND u.cena IS NOT NULL AND u.cena > 0
              AND p.hodnota IS NOT NULL AND p.hodnota > 0
    """, (ico,)).fetchall()

    if price_ratios:
        ratios = [r['cena'] / r['hodnota'] * 100 for r in price_ratios]
        avg_ratio = statistics.mean(ratios)
        aggressiveness = "agresívne" if avg_ratio < 85 else ("konzervatívne" if avg_ratio > 95 else "mierne")
        lines.append(
            f"Priemerný pomer ponukovej ceny k predpokladanej hodnote zákazky je "
            f"{fmt_pct(avg_ratio)}, čo naznačuje {aggressiveness} oceňovanie "
            f"(na základe {fmt_num(len(ratios))} zákaziek s oboma hodnotami)."
        )
        # Trend by year
        year_ratios = defaultdict(list)
        for r in price_ratios:
            if r['rok']:
                year_ratios[r['rok']].append(r['cena'] / r['hodnota'] * 100)
        if len(year_ratios) > 1:
            trend_parts = []
            for y in sorted(year_ratios.keys()):
                trend_parts.append(f"{y}: {fmt_pct(statistics.mean(year_ratios[y]))}")
            lines.append(f"Vývoj pomeru cena/hodnota: {', '.join(trend_parts)}.")
    else:
        lines.append(
            "Nebolo možné vypočítať cenový pomer — chýbajú predpokladané hodnoty zákaziek "
            "alebo ponukové ceny."
        )
    lines.append("")

    # ---- 7. ČASOVÝ VÝVOJ ----
    lines.append("7. ČASOVÝ VÝVOJ")
    lines.append("-" * 40)

    year_stats = conn.execute("""
        SELECT d.rok, COUNT(*) as cnt, SUM(u.cena) as total
        FROM ucastnici u
        JOIN dokumenty d ON u.doc_id = d.id
        WHERE u.ico = ? AND u.je_vitaz = 1 AND d.rok IS NOT NULL
        GROUP BY d.rok
        ORDER BY d.rok
    """, (ico,)).fetchall()

    if year_stats:
        prev = None
        for ys in year_stats:
            trend = ""
            if prev:
                if ys['total'] and prev['total']:
                    change = ((ys['total'] - prev['total']) / prev['total'] * 100)
                    trend_word = "nárast" if change > 0 else "pokles"
                    trend = (
                        f", čo je {trend_word} o {fmt_pct(abs(change))} oproti roku "
                        f"{prev['rok']} ({fmt_num(prev['cnt'])} zákaziek za {fmt_eur(prev['total'])})"
                    )
            lines.append(
                f"V roku {ys['rok']} firma získala {fmt_num(ys['cnt'])} zákaziek "
                f"za {fmt_eur(ys['total'] or 0)}{trend}."
            )
            prev = ys
    else:
        lines.append("Žiadne dáta o časovom vývoji.")
    lines.append("")

    # ---- 8. SIEŤOVÉ VÄZBY ----
    lines.append("8. SIEŤOVÉ VÄZBY")
    lines.append("-" * 40)

    # Find all doc_ids where this firm participated
    my_docs = conn.execute(
        "SELECT doc_id FROM ucastnici WHERE ico = ?", (ico,)
    ).fetchall()
    my_doc_ids = [d['doc_id'] for d in my_docs]

    if my_doc_ids:
        placeholders = ",".join("?" * len(my_doc_ids))
        co_bidders = conn.execute(f"""
            SELECT u.nazov, u.ico, COUNT(DISTINCT u.doc_id) as cnt,
                   SUM(CASE WHEN u.je_vitaz = 1 THEN 1 ELSE 0 END) as their_wins
            FROM ucastnici u
            WHERE u.doc_id IN ({placeholders})
                  AND u.ico != ? AND u.ico IS NOT NULL
            GROUP BY u.ico
            ORDER BY cnt DESC
            LIMIT 5
        """, my_doc_ids + [ico]).fetchall()

        # My wins in shared tenders
        my_wins_in_shared = conn.execute(f"""
            SELECT doc_id FROM ucastnici
            WHERE ico = ? AND je_vitaz = 1 AND doc_id IN ({placeholders})
        """, [ico] + my_doc_ids).fetchall()
        my_win_ids = set(r['doc_id'] for r in my_wins_in_shared)

        if co_bidders:
            for i, cb in enumerate(co_bidders):
                # How many of those shared tenders did we win vs them
                shared_docs = conn.execute(f"""
                    SELECT doc_id FROM ucastnici
                    WHERE ico = ? AND doc_id IN ({placeholders})
                """, [cb['ico']] + my_doc_ids).fetchall()
                shared_ids = set(r['doc_id'] for r in shared_docs)
                my_wins_here = len(shared_ids & my_win_ids)

                if i == 0:
                    prefix = "Firma sa najčastejšie stretáva v tendroch s firmou"
                else:
                    prefix = "Ďalším častým konkurentom je firma"
                lines.append(
                    f"{prefix} {cb['nazov']} "
                    f"(IČO: {cb['ico']}) — {fmt_num(cb['cnt'])}-krát. Z toho {nazov} "
                    f"zvíťazila {fmt_num(my_wins_here)}-krát a {cb['nazov']} "
                    f"{fmt_num(cb['their_wins'])}-krát."
                )
        else:
            lines.append("Neboli identifikované žiadne spolusúťažiace firmy.")
    else:
        lines.append("Žiadne údaje o účastiach v tendroch.")
    lines.append("")

    # ---- 9. ZMLUVY A DODATKY ----
    lines.append("9. ZMLUVY A DODATKY")
    lines.append("-" * 40)

    # Contracts (via wins)
    contracts = conn.execute("""
        SELECT d.id, d.rok, z.predmet, u.cena, zm.zmluva_id, zm.datum, zm.url
        FROM ucastnici u
        JOIN dokumenty d ON u.doc_id = d.id
        LEFT JOIN zakazky z ON u.doc_id = z.doc_id
        LEFT JOIN zmluvy zm ON u.doc_id = zm.doc_id
        WHERE u.ico = ? AND u.je_vitaz = 1
        ORDER BY d.rok DESC, u.cena DESC
    """, (ico,)).fetchall()

    if contracts:
        # Deduplicate by doc_id
        seen = set()
        lines.append(f"{'Rok':<6} {'Hodnota':>18}  {'Predmet'}")
        lines.append("-" * 72)
        for c in contracts:
            if c['id'] in seen:
                continue
            seen.add(c['id'])
            predmet = (c['predmet'] or "")[:50]
            cena = fmt_eur(c['cena']) if c['cena'] else "N/A"
            lines.append(f"{c['rok'] or '':>4}   {cena:>18}  {predmet}")
    else:
        lines.append("Žiadne zmluvy v databáze.")

    # Contract modifications
    zmeny = conn.execute("""
        SELECT zm.doc_id, zm.hodnota_po_zmene, zm.mena, zm.dovod_zmeny,
               zm.zmluva_datum, zm.zhrnutie
        FROM zmeny_zmluv zm
        WHERE zm.dodavatel_ico = ?
        ORDER BY zm.zmluva_datum
    """, (ico,)).fetchall()

    if zmeny:
        lines.append("")
        lines.append(f"Dodatky k zmluvám: {fmt_num(len(zmeny))}")
        for z in zmeny:
            dovod = z['dovod_zmeny'] or "neuvedený"
            val = fmt_eur(z['hodnota_po_zmene']) if z['hodnota_po_zmene'] else "N/A"
            lines.append(f"  - Hodnota po zmene: {val}, dôvod: {dovod}")
    lines.append("")

    # ---- 10. RIZIKOVÉ INDIKÁTORY ----
    lines.append("10. ZHRNUTIE RIZIKOVÝCH INDIKÁTOROV")
    lines.append("-" * 40)

    flags = []

    # Single-bidder rate >50%
    if total_wins_count > 0 and single_pct > 50:
        flags.append(
            f"VYSOKÝ PODIEL JEDNÉHO UCHÁDZAČA: V {fmt_pct(single_pct)} víťazných "
            f"zákaziek ({fmt_num(single_bidder)} z {fmt_num(total_wins_count)}) "
            f"bola firma jediným uchádzačom."
        )

    # One customer >70%
    if buyers and total_revenue:
        top_buyer_pct = (buyers[0]['total'] / total_revenue * 100) if buyers[0]['total'] else 0
        if top_buyer_pct > 70:
            flags.append(
                f"VYSOKÁ ZÁVISLOSŤ NA JEDNOM ZÁKAZNÍKOVI: {buyers[0]['nazov']} "
                f"tvorí {fmt_pct(top_buyer_pct)} obratu."
            )

    # Always wins against same competitor
    if my_doc_ids:
        placeholders = ",".join("?" * len(my_doc_ids))
        repeat_opponents = conn.execute(f"""
            SELECT u.nazov, u.ico, COUNT(*) as cnt,
                   SUM(CASE WHEN u.je_vitaz = 0 THEN 1 ELSE 0 END) as losses
            FROM ucastnici u
            WHERE u.doc_id IN ({placeholders})
                  AND u.ico != ? AND u.ico IS NOT NULL
            GROUP BY u.ico
            HAVING cnt >= 3 AND losses = cnt
            ORDER BY cnt DESC
        """, my_doc_ids + [ico]).fetchall()

        for ro in repeat_opponents:
            flags.append(
                f"OPAKOVANÉ VÍŤAZSTVO NAD ROVNAKÝM KONKURENTOM: "
                f"{nazov} porazila firmu {ro['nazov']} (IČO: {ro['ico']}) "
                f"vo všetkých {fmt_num(ro['cnt'])} spoločných tendroch."
            )

    # Contract modifications increasing value
    if zmeny:
        significant_zmeny = [z for z in zmeny if z['hodnota_po_zmene'] and z['hodnota_po_zmene'] > 0]
        if len(significant_zmeny) > 2:
            flags.append(
                f"DODATKY K ZMLUVÁM: {fmt_num(len(zmeny))} zmenových konaní, "
                f"čo môže indikovať opakované navyšovanie hodnoty zmlúv."
            )

    if flags:
        for f in flags:
            lines.append(f"  • {f}")
    else:
        lines.append("  Žiadne rizikové indikátory neboli identifikované.")
    lines.append("")

    return "\n".join(lines)


# ============================================================
#  OBSTARÁVATEĽ PROFILE
# ============================================================

def profile_obstaravatel(conn, nazov, ico):
    lines = []
    lines.append("=" * 72)
    lines.append(f"  PROFIL OBSTARÁVATEĽA: {nazov}")
    lines.append(f"  IČO: {ico}")
    lines.append("=" * 72)
    lines.append("")

    # ---- 1. IDENTIFIKÁCIA ----
    lines.append("1. IDENTIFIKÁCIA")
    lines.append("-" * 40)
    info = conn.execute(
        "SELECT * FROM obstaravatelia WHERE ico = ? LIMIT 1", (ico,)
    ).fetchone()
    if info:
        typ = info['typ_kupujuceho'] or "neuvedený"
        cinnost = info['cinnost'] or "neuvedená"
        adresa = info['adresa'] or ""
        mesto = info['mesto'] or ""
        psc = info['psc'] or ""
        addr_parts = [x for x in [adresa, psc, mesto] if x]
        addr_str = ", ".join(addr_parts) if addr_parts else "neuvedená"
        lines.append(
            f"{nazov}, IČO {ico}. Typ: {typ}. "
            f"Hlavná činnosť: {cinnost}. Adresa: {addr_str}."
        )
    else:
        lines.append(f"{nazov}, IČO {ico}.")
    lines.append("")

    # ---- 2. CELKOVÝ PROFIL ----
    lines.append("2. CELKOVÝ PROFIL OBSTARÁVATEĽA")
    lines.append("-" * 40)

    tenders = conn.execute("""
        SELECT d.id, d.rok, d.action, v.celkova_hodnota, v.mena,
               z.predmet, v.pocet_ponuk
        FROM obstaravatelia o
        JOIN dokumenty d ON o.doc_id = d.id
        LEFT JOIN vysledky v ON o.doc_id = v.doc_id
        LEFT JOIN zakazky z ON o.doc_id = z.doc_id
        WHERE o.ico = ?
        ORDER BY d.rok
    """, (ico,)).fetchall()

    # Results only
    results = [t for t in tenders if t['action'] == 'vysledok']
    all_years = sorted(set(t['rok'] for t in tenders if t['rok']))
    result_values = [t['celkova_hodnota'] for t in results if t['celkova_hodnota'] and t['celkova_hodnota'] > 0]
    total_val = sum(result_values)

    lines.append(
        f"Obstarávateľ {nazov} eviduje v databáze celkovo {fmt_num(len(tenders))} dokumentov "
        f"({fmt_num(len(results))} výsledkov). "
    )
    if all_years:
        lines.append(
            f"Aktivita je zaznamenaná od roku {min(all_years)} do roku {max(all_years)}. "
            f"Celková hodnota zadaných zákaziek je {fmt_eur(total_val)}."
        )
    lines.append("")

    # ---- 3. DVORNÍ DODÁVATELIA ----
    lines.append("3. DVORNÍ DODÁVATELIA")
    lines.append("-" * 40)

    suppliers = conn.execute("""
        SELECT u.nazov, u.ico, COUNT(*) as cnt, SUM(u.cena) as total
        FROM obstaravatelia o
        JOIN ucastnici u ON o.doc_id = u.doc_id
        WHERE o.ico = ? AND u.je_vitaz = 1
        GROUP BY u.ico
        ORDER BY cnt DESC
    """, (ico,)).fetchall()

    total_supplier_val = sum(s['total'] for s in suppliers if s['total'])
    total_supplier_cnt = sum(s['cnt'] for s in suppliers)

    if suppliers:
        # HHI calculation
        hhi = 0
        for s in suppliers:
            if total_supplier_val and s['total']:
                share = s['total'] / total_supplier_val * 100
                hhi += share ** 2

        hhi_level = "nízkej" if hhi < 1500 else ("strednej" if hhi < 2500 else "vysokej")

        for i, s in enumerate(suppliers[:5]):
            pct_cnt = (s['cnt'] / total_supplier_cnt * 100) if total_supplier_cnt else 0
            pct_val = (s['total'] / total_supplier_val * 100) if total_supplier_val and s['total'] else 0
            if i == 0:
                lines.append(
                    f"Najčastejším dodávateľom pre {nazov} je firma {s['nazov']} "
                    f"(IČO: {s['ico']}) s {fmt_num(s['cnt'])} zákazkami "
                    f"({fmt_pct(pct_cnt)} zo všetkých zákaziek). "
                    f"Herfindahlov index koncentrácie dodávateľov je {fmt_num(round(hhi))}, "
                    f"čo zodpovedá {hhi_level} koncentrácii."
                )
            else:
                lines.append(
                    f"Ďalším dodávateľom je {s['nazov']} ({fmt_num(s['cnt'])} zákaziek, "
                    f"{fmt_eur(s['total'] or 0)}, {fmt_pct(pct_val)})."
                )
    else:
        lines.append("Žiadni dodávatelia neboli identifikovaní.")
    lines.append("")

    # ---- 4. ŠTRUKTÚRA NÁKUPOV ----
    lines.append("4. ŠTRUKTÚRA NÁKUPOV")
    lines.append("-" * 40)

    cpv_data = conn.execute("""
        SELECT z.cpv_kod, z.druh, COUNT(*) as cnt
        FROM obstaravatelia o
        JOIN zakazky z ON o.doc_id = z.doc_id
        WHERE o.ico = ? AND z.cpv_kod IS NOT NULL
        GROUP BY z.cpv_kod
        ORDER BY cnt DESC
    """, (ico,)).fetchall()

    druh_data = conn.execute("""
        SELECT z.druh, COUNT(*) as cnt
        FROM obstaravatelia o
        JOIN zakazky z ON o.doc_id = z.doc_id
        WHERE o.ico = ? AND z.druh IS NOT NULL AND z.druh != ''
        GROUP BY z.druh
        ORDER BY cnt DESC
    """, (ico,)).fetchall()

    postup_data = conn.execute("""
        SELECT z.druh_postupu, COUNT(*) as cnt
        FROM obstaravatelia o
        JOIN zakazky z ON o.doc_id = z.doc_id
        WHERE o.ico = ? AND z.druh_postupu IS NOT NULL AND z.druh_postupu != ''
        GROUP BY z.druh_postupu
        ORDER BY cnt DESC
    """, (ico,)).fetchall()

    total_docs = len(tenders)
    if cpv_data:
        top = cpv_data[0]
        pct = (top['cnt'] / total_docs * 100) if total_docs else 0
        lines.append(
            f"Obstarávateľ nakupuje primárne v kategórii CPV {top['cpv_kod']} "
            f"({fmt_pct(pct)} zákaziek)."
        )
        for c in cpv_data[1:3]:
            lines.append(f"Ďalšia kategória: CPV {c['cpv_kod']} ({fmt_num(c['cnt'])} zákaziek).")

    if druh_data:
        druh_list = ", ".join(f"{d['druh']} ({fmt_num(d['cnt'])})" for d in druh_data)
        lines.append(f"Druhy zákaziek: {druh_list}.")

    if postup_data:
        postup_list = ", ".join(f"{p['druh_postupu']} ({fmt_num(p['cnt'])})" for p in postup_data[:3])
        lines.append(f"Druhy postupov: {postup_list}.")
    lines.append("")

    # ---- 5. KONKURENČNOSŤ ----
    lines.append("5. KONKURENČNOSŤ")
    lines.append("-" * 40)

    comp_stats = conn.execute("""
        SELECT AVG(v.pocet_ponuk) as avg_ponuk,
               COUNT(CASE WHEN v.pocet_ponuk = 1 THEN 1 END) as single_bid,
               COUNT(*) as total,
               SUM(CASE WHEN v.elektronicke_ponuky = 1 THEN 1 ELSE 0 END) as e_ponuky
        FROM obstaravatelia o
        JOIN vysledky v ON o.doc_id = v.doc_id
        WHERE o.ico = ? AND v.pocet_ponuk IS NOT NULL
    """, (ico,)).fetchone()

    # E-auction from prilezitosti
    eauction = conn.execute("""
        SELECT COUNT(CASE WHEN p.elektronicka_aukcia = 1 THEN 1 END) as cnt,
               COUNT(*) as total
        FROM obstaravatelia o
        JOIN prilezitosti p ON o.doc_id = p.doc_id
        WHERE o.ico = ?
    """, (ico,)).fetchone()

    if comp_stats and comp_stats['total'] > 0:
        avg_ponuk = comp_stats['avg_ponuk'] or 0
        single_pct = (comp_stats['single_bid'] / comp_stats['total'] * 100)
        lines.append(
            f"Priemerný počet ponúk na tender je {fmt_num(round(avg_ponuk, 1))}. "
            f"V {fmt_num(comp_stats['single_bid'])} prípadoch ({fmt_pct(single_pct)}) "
            f"bol podaný iba jeden ponúk."
        )
        if eauction and eauction['total'] > 0:
            ea_pct = (eauction['cnt'] / eauction['total'] * 100)
            lines.append(
                f"Elektronická aukcia bola použitá v {fmt_num(eauction['cnt'])} "
                f"prípadoch ({fmt_pct(ea_pct)})."
            )
    else:
        lines.append("Nedostatok dát pre analýzu konkurenčnosti.")
    lines.append("")

    # ---- 6. CENOVÁ EFEKTÍVNOSŤ ----
    lines.append("6. CENOVÁ EFEKTÍVNOSŤ")
    lines.append("-" * 40)

    price_data = conn.execute("""
        SELECT v.celkova_hodnota, p.hodnota
        FROM obstaravatelia o
        JOIN vysledky v ON o.doc_id = v.doc_id
        JOIN prilezitosti p ON o.doc_id = p.doc_id
        WHERE o.ico = ?
              AND v.celkova_hodnota IS NOT NULL AND v.celkova_hodnota > 0
              AND p.hodnota IS NOT NULL AND p.hodnota > 0
    """, (ico,)).fetchall()

    if price_data:
        ratios = [r['celkova_hodnota'] / r['hodnota'] * 100 for r in price_data]
        avg_ratio = statistics.mean(ratios)
        below_estimate = sum(1 for r in ratios if r < 100)
        below_pct = (below_estimate / len(ratios) * 100)
        lines.append(
            f"V priemere je víťazná cena na úrovni {fmt_pct(avg_ratio)} "
            f"predpokladanej hodnoty (na základe {fmt_num(len(ratios))} zákaziek). "
            f"V {fmt_num(below_estimate)} prípadoch ({fmt_pct(below_pct)}) bola zákazka "
            f"zadaná pod odhadovanú hodnotu."
        )
    else:
        lines.append(
            "Nebolo možné vypočítať cenovú efektívnosť — chýbajú párované údaje "
            "o predpokladanej a výslednej hodnote."
        )
    lines.append("")

    # ---- 7. ČASOVÝ VÝVOJ ----
    lines.append("7. ČASOVÝ VÝVOJ")
    lines.append("-" * 40)

    year_stats = conn.execute("""
        SELECT d.rok, COUNT(*) as cnt, SUM(v.celkova_hodnota) as total
        FROM obstaravatelia o
        JOIN dokumenty d ON o.doc_id = d.id
        LEFT JOIN vysledky v ON o.doc_id = v.doc_id
        WHERE o.ico = ? AND d.rok IS NOT NULL AND d.action = 'vysledok'
        GROUP BY d.rok
        ORDER BY d.rok
    """, (ico,)).fetchall()

    if year_stats:
        prev = None
        for ys in year_stats:
            trend = ""
            if prev:
                if ys['total'] and prev['total'] and prev['total'] > 0:
                    change = ((ys['total'] - prev['total']) / prev['total'] * 100)
                    trend_word = "nárast" if change > 0 else "pokles"
                    trend = (
                        f", čo je {trend_word} o {fmt_pct(abs(change))} oproti roku "
                        f"{prev['rok']} ({fmt_num(prev['cnt'])} zákaziek za {fmt_eur(prev['total'])})"
                    )
            lines.append(
                f"V roku {ys['rok']} bolo zadaných {fmt_num(ys['cnt'])} zákaziek "
                f"za {fmt_eur(ys['total'] or 0)}{trend}."
            )
            prev = ys
    else:
        lines.append("Žiadne dáta o časovom vývoji.")
    lines.append("")

    # ---- 8. ZMLUVY A DODATKY ----
    lines.append("8. ZMLUVY A DODATKY")
    lines.append("-" * 40)

    zmeny = conn.execute("""
        SELECT zm.doc_id, zm.dodavatel_nazov, zm.dodavatel_ico,
               zm.hodnota_po_zmene, zm.mena, zm.dovod_zmeny, zm.zmluva_datum
        FROM obstaravatelia o
        JOIN zmeny_zmluv zm ON o.doc_id = zm.doc_id
        WHERE o.ico = ?
        ORDER BY zm.zmluva_datum
    """, (ico,)).fetchall()

    if zmeny:
        total_zmeny_val = sum(z['hodnota_po_zmene'] for z in zmeny if z['hodnota_po_zmene'])
        lines.append(
            f"Obstarávateľ uzatvoril {fmt_num(len(zmeny))} dodatkov k zmluvám, "
            f"pričom celková hodnota zmien predstavuje {fmt_eur(total_zmeny_val)}."
        )
        lines.append("")
        lines.append(f"{'Dodávateľ':<30} {'Hodnota po zmene':>18}  {'Dôvod'}")
        lines.append("-" * 72)
        for z in zmeny:
            dod = (z['dodavatel_nazov'] or "")[:28]
            val = fmt_eur(z['hodnota_po_zmene']) if z['hodnota_po_zmene'] else "N/A"
            dovod = (z['dovod_zmeny'] or "")[:20]
            lines.append(f"{dod:<30} {val:>18}  {dovod}")
    else:
        lines.append("Žiadne dodatky k zmluvám v databáze.")
    lines.append("")

    # ---- 9. RIZIKOVÉ INDIKÁTORY ----
    lines.append("9. ZHRNUTIE RIZIKOVÝCH INDIKÁTOROV")
    lines.append("-" * 40)

    flags = []

    # Single-bidder rate > 50%
    if comp_stats and comp_stats['total'] > 0:
        sb_pct = (comp_stats['single_bid'] / comp_stats['total'] * 100)
        if sb_pct > 50:
            flags.append(
                f"VYSOKÝ PODIEL JEDNÉHO UCHÁDZAČA: {fmt_pct(sb_pct)} tendrov "
                f"({fmt_num(comp_stats['single_bid'])} z {fmt_num(comp_stats['total'])}) "
                f"malo iba jedného uchádzača."
            )

    # Top supplier >70%
    if suppliers and total_supplier_cnt:
        top_sup_pct = (suppliers[0]['cnt'] / total_supplier_cnt * 100)
        if top_sup_pct > 70:
            flags.append(
                f"VYSOKÁ KONCENTRÁCIA DODÁVATEĽA: {suppliers[0]['nazov']} "
                f"získal {fmt_pct(top_sup_pct)} všetkých zákaziek."
            )

    # High HHI
    if suppliers and total_supplier_val:
        hhi = sum((s['total'] / total_supplier_val * 100) ** 2
                   for s in suppliers if s['total'])
        if hhi > 2500:
            flags.append(
                f"VYSOKÁ KONCENTRÁCIA DODÁVATEĽOV: HHI = {fmt_num(round(hhi))} "
                f"(prahová hodnota pre vysokú koncentráciu: 2 500)."
            )

    # Many contract modifications
    if len(zmeny) > 5:
        flags.append(
            f"VEĽKÝ POČET DODATKOV: {fmt_num(len(zmeny))} zmenových konaní."
        )

    if flags:
        for f in flags:
            lines.append(f"  • {f}")
    else:
        lines.append("  Žiadne rizikové indikátory neboli identifikované.")
    lines.append("")

    return "\n".join(lines)


# ============================================================
#  MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Profilový nástroj pre dodávateľov a obstarávateľov z UVO Vestníka."
    )
    parser.add_argument("--dodavatel", "-d", help="IČO alebo názov dodávateľa")
    parser.add_argument("--obstaravatel", "-o", help="IČO alebo názov obstarávateľa")
    args = parser.parse_args()

    if not args.dodavatel and not args.obstaravatel:
        parser.print_help()
        sys.exit(1)

    conn = connect_db()

    if args.dodavatel:
        nazov, ico = resolve_dodavatel(conn, args.dodavatel)
        if not ico:
            print(f"CHYBA: Dodávateľ '{args.dodavatel}' nebol nájdený v databáze.")
            sys.exit(1)
        print(f"Nájdený dodávateľ: {nazov} (IČO: {ico})")
        print()
        report = profile_dodavatel(conn, nazov, ico)
    else:
        nazov, ico = resolve_obstaravatel(conn, args.obstaravatel)
        if not ico:
            print(f"CHYBA: Obstarávateľ '{args.obstaravatel}' nebol nájdený v databáze.")
            sys.exit(1)
        print(f"Nájdený obstarávateľ: {nazov} (IČO: {ico})")
        print()
        report = profile_obstaravatel(conn, nazov, ico)

    # Print to terminal
    print(report)

    # Save to file
    os.makedirs(REPORTS_DIR, exist_ok=True)
    filepath = os.path.join(REPORTS_DIR, f"profil_{ico}.txt")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nReport uložený: {filepath}")

    conn.close()


if __name__ == "__main__":
    main()
