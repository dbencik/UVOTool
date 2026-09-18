#!/usr/bin/env python3
"""
vestnik-graph.py — Relationship graph analysis of UVO procurement data.

Builds and analyses three graph types from vestnik_*.json parser results:
  1. Bipartite buyer↔winner graph
  2. Co-bidding graph
  3. Geographic cross-analysis

Usage:
    python3 tools/vestnik-graph.py
    python3 tools/vestnik-graph.py --db data/vestnik.db          # use existing DB
    python3 tools/vestnik-graph.py --rebuild                      # force DB rebuild
"""

import sys
import os
import json
import glob
import sqlite3
import argparse
import re
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
try:
    import networkx as nx
except ImportError:
    print("ERROR: networkx is not installed.")
    print("Install it with:  pip install networkx")
    sys.exit(1)

try:
    import community as community_louvain  # python-louvain
    HAS_LOUVAIN = True
except ImportError:
    HAS_LOUVAIN = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "vestnik.db"
RESULTS_DIR = REPO_ROOT / "data" / "results"
JSON_GLOB = str(RESULTS_DIR / "vestnik_*_parser_results.json")

# ---------------------------------------------------------------------------
# DB bootstrap — build from JSON parser results
# ---------------------------------------------------------------------------

def _norm_ico(ico: str) -> str:
    """Return cleaned IČO string or empty string."""
    if not ico:
        return ""
    s = re.sub(r"\s+", "", str(ico))
    return s if re.fullmatch(r"\d{6,10}", s) else ""


def build_db(db_path: Path, json_glob: str) -> int:
    """Parse all vestnik JSON files and load into SQLite. Returns doc count."""
    files = sorted(glob.glob(json_glob))
    if not files:
        print(f"WARNING: No JSON files found matching {json_glob}")
        return 0

    con = sqlite3.connect(str(db_path))
    cur = con.cursor()

    cur.executescript("""
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS dokumenty (
            doc_id      TEXT PRIMARY KEY,
            vestnik     TEXT,
            action      TEXT,
            url         TEXT,
            id_zakazky  TEXT
        );

        CREATE TABLE IF NOT EXISTS obstaravatelia (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id      TEXT,
            nazov       TEXT,
            ico         TEXT,
            mesto       TEXT,
            typ         TEXT
        );

        CREATE TABLE IF NOT EXISTS zakazky (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id      TEXT,
            predmet     TEXT,
            druh        TEXT,
            nuts        TEXT
        );

        CREATE TABLE IF NOT EXISTS vysledky (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id      TEXT,
            celkova_hodnota REAL,
            mena        TEXT,
            pocet_ponuk INTEGER
        );

        CREATE TABLE IF NOT EXISTS ucastnici (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id      TEXT,
            nazov       TEXT,
            ico         TEXT,
            cena        REAL,
            poradie     INTEGER,
            je_vitaz    INTEGER
        );

        CREATE TABLE IF NOT EXISTS zmeny_zmluv (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id              TEXT,
            dodavatel_nazov     TEXT,
            dodavatel_ico       TEXT,
            hodnota_po_zmene    REAL,
            mena                TEXT
        );
    """)
    con.commit()

    loaded = 0
    skipped = 0

    for filepath in files:
        with open(filepath, encoding="utf-8") as fh:
            try:
                docs = json.load(fh)
            except json.JSONDecodeError as exc:
                print(f"  SKIP {filepath}: JSON error — {exc}")
                continue

        for doc in docs:
            doc_id = str(doc.get("id", "")).strip()
            if not doc_id:
                skipped += 1
                continue

            # Upsert document row
            cur.execute(
                "INSERT OR REPLACE INTO dokumenty(doc_id, vestnik, action, url, id_zakazky) "
                "VALUES (?,?,?,?,?)",
                (
                    doc_id,
                    doc.get("vestnik", ""),
                    doc.get("action", ""),
                    doc.get("url", ""),
                    doc.get("extraction", {}).get("metadata", {}).get("id_zakazky", ""),
                ),
            )

            ext = doc.get("extraction", {})
            obs = ext.get("obstaravatel", {})
            zakazka = ext.get("zakazka", {})

            # Delete old dependent rows (for replace semantics)
            for tbl in ("obstaravatelia", "zakazky", "vysledky", "ucastnici", "zmeny_zmluv"):
                cur.execute(f"DELETE FROM {tbl} WHERE doc_id=?", (doc_id,))

            # obstaravatel
            if obs.get("nazov") or obs.get("ico"):
                cur.execute(
                    "INSERT INTO obstaravatelia(doc_id, nazov, ico, mesto, typ) VALUES (?,?,?,?,?)",
                    (
                        doc_id,
                        obs.get("nazov", ""),
                        _norm_ico(obs.get("ico", "")),
                        obs.get("mesto", ""),
                        obs.get("typ_kupujuceho", ""),
                    ),
                )

            # zakazka
            if zakazka.get("predmet"):
                cur.execute(
                    "INSERT INTO zakazky(doc_id, predmet, druh, nuts) VALUES (?,?,?,?)",
                    (
                        doc_id,
                        zakazka.get("predmet", ""),
                        zakazka.get("druh", ""),
                        zakazka.get("nuts", ""),
                    ),
                )

            action = doc.get("action", "")

            # vysledok
            if action == "vysledok":
                vys = ext.get("vysledok", {})
                cur.execute(
                    "INSERT INTO vysledky(doc_id, celkova_hodnota, mena, pocet_ponuk) VALUES (?,?,?,?)",
                    (
                        doc_id,
                        vys.get("celkova_hodnota"),
                        vys.get("mena", "EUR"),
                        vys.get("pocet_ponuk"),
                    ),
                )
                for u in vys.get("ucastnici", []):
                    ico = _norm_ico(u.get("ico", ""))
                    cur.execute(
                        "INSERT INTO ucastnici(doc_id, nazov, ico, cena, poradie, je_vitaz) "
                        "VALUES (?,?,?,?,?,?)",
                        (
                            doc_id,
                            u.get("nazov", ""),
                            ico,
                            u.get("cena"),
                            u.get("poradie"),
                            1 if u.get("je_vitaz") else 0,
                        ),
                    )

            # zmena_zmluvy
            if action == "zmena_zmluvy":
                zm = ext.get("zmena_zmluvy", {})
                dod = zm.get("dodavatel", {})
                cur.execute(
                    "INSERT INTO zmeny_zmluv(doc_id, dodavatel_nazov, dodavatel_ico, "
                    "hodnota_po_zmene, mena) VALUES (?,?,?,?,?)",
                    (
                        doc_id,
                        dod.get("nazov", ""),
                        _norm_ico(dod.get("ico", "")),
                        zm.get("hodnota_po_zmene"),
                        zm.get("mena", "EUR"),
                    ),
                )

            loaded += 1

    con.commit()
    con.close()

    print(f"  Loaded {loaded} documents from {len(files)} files ({skipped} skipped — no ID).")
    return loaded


# ---------------------------------------------------------------------------
# Schema detection — existing DB may use 'id' instead of 'doc_id' in dokumenty
# ---------------------------------------------------------------------------

def _doc_pk(con: sqlite3.Connection) -> str:
    """Return the primary key column name for the dokumenty table."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(dokumenty)").fetchall()]
    return "doc_id" if "doc_id" in cols else "id"


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def load_buyer_winner_pairs(con: sqlite3.Connection):
    """
    Returns list of dicts: {doc_id, buyer_ico, buyer_nazov, buyer_mesto,
                             winner_ico, winner_nazov, cena}
    Only vysledok documents with both buyer IČO and winner IČO.
    """
    pk = _doc_pk(con)
    sql = f"""
        SELECT
            d.{pk},
            o.ico  AS buyer_ico,
            o.nazov AS buyer_nazov,
            o.mesto AS buyer_mesto,
            u.ico  AS winner_ico,
            u.nazov AS winner_nazov,
            u.cena
        FROM dokumenty d
        JOIN obstaravatelia o ON o.doc_id = d.{pk}
        JOIN ucastnici u ON u.doc_id = d.{pk} AND u.je_vitaz = 1
        WHERE d.action = 'vysledok'
          AND o.ico != ''
          AND u.ico != ''
    """
    rows = con.execute(sql).fetchall()
    cols = ["doc_id","buyer_ico","buyer_nazov","buyer_mesto","winner_ico","winner_nazov","cena"]
    return [dict(zip(cols, r)) for r in rows]


def load_all_bidders_per_tender(con: sqlite3.Connection):
    """
    Returns dict: doc_id → list of {ico, nazov, je_vitaz, cena}
    Only tenders with ≥2 bidders that have IČO.
    """
    pk = _doc_pk(con)
    sql = f"""
        SELECT d.{pk}, u.ico, u.nazov, u.je_vitaz, u.cena
        FROM dokumenty d
        JOIN ucastnici u ON u.doc_id = d.{pk}
        WHERE d.action = 'vysledok' AND u.ico != ''
    """
    rows = con.execute(sql).fetchall()
    tenders = defaultdict(list)
    for doc_id, ico, nazov, je_vitaz, cena in rows:
        tenders[doc_id].append({"ico": ico, "nazov": nazov, "je_vitaz": je_vitaz, "cena": cena})
    # keep only tenders with ≥2 participants
    return {k: v for k, v in tenders.items() if len(v) >= 2}


def load_company_names(con: sqlite3.Connection) -> dict:
    """Returns ico → best known name mapping."""
    names = {}
    for ico, nazov in con.execute("SELECT ico, nazov FROM ucastnici WHERE ico != ''"):
        if ico and nazov:
            names[ico] = nazov
    for ico, nazov in con.execute("SELECT ico, nazov FROM obstaravatelia WHERE ico != ''"):
        if ico and nazov:
            names[ico] = nazov
    for ico, nazov in con.execute("SELECT dodavatel_ico, dodavatel_nazov FROM zmeny_zmluv WHERE dodavatel_ico != ''"):
        if ico and nazov:
            names[ico] = nazov
    return names


def load_company_cities(con: sqlite3.Connection) -> dict:
    """Returns ico → mesto mapping (buyers only — bidders rarely have city in data)."""
    cities = {}
    for ico, mesto in con.execute("SELECT ico, mesto FROM obstaravatelia WHERE ico != '' AND mesto != ''"):
        if ico:
            cities[ico] = mesto
    return cities


# ---------------------------------------------------------------------------
# Graph 1: Bipartite buyer↔winner
# ---------------------------------------------------------------------------

def build_buyer_winner_graph(pairs: list, company_names: dict, company_cities: dict) -> nx.DiGraph:
    """Directed weighted graph: buyer → winner."""
    G = nx.DiGraph()

    # Track roles and values per IČO
    ico_types: dict[str, set] = defaultdict(set)
    ico_value: dict[str, float] = defaultdict(float)

    for p in pairs:
        b, w = p["buyer_ico"], p["winner_ico"]
        cena = p["cena"] or 0.0

        ico_types[b].add("buyer")
        ico_types[w].add("winner")
        ico_value[b] += cena
        ico_value[w] += cena

        if G.has_edge(b, w):
            G[b][w]["weight"] += 1
            G[b][w]["total_value"] += cena
        else:
            G.add_edge(b, w, weight=1, total_value=cena)

    # Add node attributes
    all_icos = set(ico_types.keys())
    for ico in all_icos:
        roles = ico_types[ico]
        if "buyer" in roles and "winner" in roles:
            ntype = "both"
        elif "buyer" in roles:
            ntype = "buyer"
        else:
            ntype = "winner"

        G.add_node(
            ico,
            name=company_names.get(ico, ico),
            type=ntype,
            total_value=round(ico_value[ico], 2),
            mesto=company_cities.get(ico, ""),
        )

    return G


# ---------------------------------------------------------------------------
# Graph 2: Co-bidding
# ---------------------------------------------------------------------------

def build_cobidding_graph(tenders_by_doc: dict, company_names: dict) -> nx.Graph:
    """Undirected weighted graph: bidders who appeared in the same tender."""
    G = nx.Graph()

    # Track per-pair how many times they co-bid and who won
    pair_cobids: dict[tuple, int] = defaultdict(int)
    pair_wins: dict[tuple, dict] = defaultdict(lambda: {"a_wins": 0, "b_wins": 0})

    for doc_id, bidders in tenders_by_doc.items():
        icos = [b["ico"] for b in bidders]
        winners = {b["ico"] for b in bidders if b["je_vitaz"]}

        for i in range(len(icos)):
            for j in range(i + 1, len(icos)):
                a, b_ = icos[i], icos[j]
                pair = (min(a, b_), max(a, b_))
                pair_cobids[pair] += 1
                if a in winners:
                    pair_wins[pair]["a_wins"] += 1
                if b_ in winners:
                    pair_wins[pair]["b_wins"] += 1

    for (a, b_), count in pair_cobids.items():
        wins = pair_wins[(a, b_)]
        G.add_edge(
            a, b_,
            weight=count,
            a_wins=wins["a_wins"],
            b_wins=wins["b_wins"],
        )

    # Node attributes
    for ico in list(G.nodes()):
        G.nodes[ico]["name"] = company_names.get(ico, ico)

    return G


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def _pagerank_safe(G: nx.Graph, **kwargs) -> dict:
    try:
        # Try scipy-backed first, fall back to pure numpy, then pure Python
        return nx.pagerank(G, **kwargs)
    except ModuleNotFoundError:
        # scipy not available — use pure Python power iteration
        try:
            return nx.pagerank(G, weight="weight")
        except Exception:
            pass
        # Manual power iteration fallback
        nodes = list(G.nodes())
        n = len(nodes)
        if n == 0:
            return {}
        alpha = kwargs.get("alpha", 0.85)
        tol = 1e-6
        max_iter = 100
        pr = {node: 1.0 / n for node in nodes}
        dangling_nodes = [node for node in nodes if G.out_degree(node) == 0]
        for _ in range(max_iter):
            pr_last = pr.copy()
            dangling_sum = alpha * sum(pr_last[n_] for n_ in dangling_nodes) / n
            for node in nodes:
                in_sum = sum(
                    pr_last[nbr] * d.get("weight", 1) / max(G.out_degree(nbr, weight="weight"), 1)
                    for nbr, d in (
                        [(u, G[u][node]) for u in G.predecessors(node)]
                        if isinstance(G, nx.DiGraph)
                        else [(u, G[u][node]) for u in G.neighbors(node)]
                    )
                )
                pr[node] = dangling_sum + (1.0 - alpha) / n + alpha * in_sum
            err = sum(abs(pr[node] - pr_last[node]) for node in nodes)
            if err < n * tol:
                break
        return pr
    except (nx.PowerIterationFailedConvergence, ZeroDivisionError):
        n = G.number_of_nodes()
        return {node: 1.0 / n for node in G.nodes()} if n > 0 else {}


def analyse_buyer_winner(G: nx.DiGraph, company_names: dict):
    """Run centrality, PageRank, component and self-dealing analysis."""
    results = {}
    n, e = G.number_of_nodes(), G.number_of_edges()
    results["summary"] = {"nodes": n, "edges": e}

    if n == 0:
        results["warning"] = "Graph is empty — no buyer↔winner pairs with IČO found."
        return results

    # Degree centrality (undirected view for total connections)
    undirected = G.to_undirected()
    deg_centrality = nx.degree_centrality(undirected)
    top_deg = sorted(deg_centrality.items(), key=lambda x: -x[1])[:20]
    results["top_degree"] = [
        {"ico": ico, "name": company_names.get(ico, ico), "degree_centrality": round(v, 4)}
        for ico, v in top_deg
    ]

    # PageRank
    pr = _pagerank_safe(G)
    top_pr = sorted(pr.items(), key=lambda x: -x[1])[:20]
    results["top_pagerank"] = [
        {
            "ico": ico,
            "name": company_names.get(ico, ico),
            "pagerank": round(v, 6),
            "type": G.nodes[ico].get("type", "?"),
            "total_value": G.nodes[ico].get("total_value", 0),
        }
        for ico, v in top_pr
    ]

    # Connected components (undirected)
    components = list(nx.connected_components(undirected))
    components.sort(key=lambda c: -len(c))
    results["components"] = {
        "count": len(components),
        "largest_size": len(components[0]) if components else 0,
        "sizes": [len(c) for c in components[:10]],
    }

    # Companies that are BOTH buyer and winner — self-dealing risk
    self_dealing = [
        {
            "ico": ico,
            "name": data.get("name", ico),
            "total_value": data.get("total_value", 0),
            "buyer_contracts": G.in_degree(ico),
            "winner_contracts": G.out_degree(ico) if G.has_node(ico) else 0,
        }
        for ico, data in G.nodes(data=True)
        if data.get("type") == "both"
    ]
    self_dealing.sort(key=lambda x: -x["total_value"])
    results["self_dealing"] = self_dealing

    # Top edges by weight
    top_edges = sorted(G.edges(data=True), key=lambda e: -e[2].get("weight", 0))[:20]
    results["top_edges"] = [
        {
            "buyer_ico": u,
            "buyer_name": company_names.get(u, u),
            "winner_ico": v,
            "winner_name": company_names.get(v, v),
            "contracts": d.get("weight", 0),
            "total_value": round(d.get("total_value", 0), 2),
        }
        for u, v, d in top_edges
    ]

    return results


def analyse_cobidding(G: nx.Graph, company_names: dict):
    """Community detection and bid-rigging pattern detection."""
    results = {}
    n, e = G.number_of_nodes(), G.number_of_edges()
    results["summary"] = {"nodes": n, "edges": e}

    if n == 0:
        results["warning"] = "Graph is empty — no tenders with ≥2 identified bidders."
        return results

    # Community detection
    if HAS_LOUVAIN:
        partition = community_louvain.best_partition(G)
        communities = defaultdict(list)
        for node, comm_id in partition.items():
            communities[comm_id].append(node)
        comm_list = sorted(communities.values(), key=lambda c: -len(c))
        results["community_method"] = "Louvain"
    else:
        comm_list = [list(c) for c in nx.connected_components(G)]
        comm_list.sort(key=lambda c: -len(c))
        results["community_method"] = "connected_components (install python-louvain for Louvain)"

    results["communities"] = {
        "count": len(comm_list),
        "largest_size": len(comm_list[0]) if comm_list else 0,
        "top_communities": [
            {
                "size": len(c),
                "members": [
                    {"ico": ico, "name": company_names.get(ico, ico)} for ico in c[:10]
                ],
            }
            for c in comm_list[:5]
        ],
    }

    # Bid-rigging signals:
    # Pairs who co-bid frequently where ONE always wins and the other never does
    suspicious_pairs = []
    for u, v, data in G.edges(data=True):
        cobids = data.get("weight", 0)
        if cobids < 2:
            continue
        a_wins = data.get("a_wins", 0)
        b_wins = data.get("b_wins", 0)
        # One-sided winning: one party wins every encounter
        if cobids >= 2 and (a_wins == 0 or b_wins == 0) and (a_wins + b_wins) > 0:
            loser = u if a_wins == 0 else v
            winner = v if a_wins == 0 else u
            suspicious_pairs.append(
                {
                    "co_bids": cobids,
                    "consistent_winner_ico": winner,
                    "consistent_winner_name": company_names.get(winner, winner),
                    "consistent_loser_ico": loser,
                    "consistent_loser_name": company_names.get(loser, loser),
                    "winner_wins": max(a_wins, b_wins),
                    "loser_wins": 0,
                }
            )

    suspicious_pairs.sort(key=lambda x: -x["co_bids"])
    results["bid_rigging_signals"] = suspicious_pairs[:20]

    # Top co-bidding pairs
    top_pairs = sorted(G.edges(data=True), key=lambda e: -e[2].get("weight", 0))[:20]
    results["top_cobidding_pairs"] = [
        {
            "company_a_ico": u,
            "company_a_name": company_names.get(u, u),
            "company_b_ico": v,
            "company_b_name": company_names.get(v, v),
            "co_bids": d.get("weight", 0),
            "a_wins": d.get("a_wins", 0),
            "b_wins": d.get("b_wins", 0),
        }
        for u, v, d in top_pairs
    ]

    return results


# ---------------------------------------------------------------------------
# Graph 3: Geographic analysis (no graph needed — pure SQL + logic)
# ---------------------------------------------------------------------------

def analyse_geography(con: sqlite3.Connection, company_names: dict):
    """Flag distant winners relative to buyer location."""
    pk = _doc_pk(con)

    # Check if zakazky has a nuts column (schema varies between DB versions)
    zakazky_cols = [r[1] for r in con.execute("PRAGMA table_info(zakazky)").fetchall()]
    has_nuts = "nuts" in zakazky_cols

    if has_nuts:
        nuts_select = "z.nuts"
        nuts_join = f"LEFT JOIN zakazky z ON z.doc_id = d.{pk}"
    else:
        nuts_select = "NULL AS nuts"
        nuts_join = ""

    sql = f"""
        SELECT
            d.{pk},
            o.nazov  AS buyer_nazov,
            o.ico    AS buyer_ico,
            o.mesto  AS buyer_mesto,
            u.ico    AS winner_ico,
            u.nazov  AS winner_nazov,
            u.cena,
            {nuts_select}
        FROM dokumenty d
        JOIN obstaravatelia o ON o.doc_id = d.{pk}
        JOIN ucastnici u ON u.doc_id = d.{pk} AND u.je_vitaz = 1
        {nuts_join}
        WHERE d.action = 'vysledok'
          AND o.ico != '' AND u.ico != ''
          AND o.mesto != ''
    """
    rows = con.execute(sql).fetchall()
    cols = ["doc_id","buyer_nazov","buyer_ico","buyer_mesto","winner_ico","winner_nazov","cena","nuts"]
    rows = [dict(zip(cols, r)) for r in rows]

    # We don't have winner city in the parser output, so we use NUTS (region)
    # to flag cases where the region is known but try to spot repeat patterns.
    # Group by buyer_mesto → count unique winners
    buyer_winners: dict[str, set] = defaultdict(set)
    for r in rows:
        buyer_winners[r["buyer_mesto"]].add(r["winner_ico"])

    # Find cities that always use the same winner (potential favouritism)
    loyal_buyers = []
    for city, winners in buyer_winners.items():
        if len(winners) == 1:
            # Find total contracts
            contracts = [r for r in rows if r["buyer_mesto"] == city]
            winner_ico = list(winners)[0]
            total_value = sum(r["cena"] or 0 for r in contracts)
            if len(contracts) >= 2:
                loyal_buyers.append(
                    {
                        "buyer_mesto": city,
                        "winner_ico": winner_ico,
                        "winner_name": company_names.get(winner_ico, winner_ico),
                        "contracts": len(contracts),
                        "total_value": round(total_value, 2),
                        "buyers": list({r["buyer_nazov"] for r in contracts}),
                    }
                )

    loyal_buyers.sort(key=lambda x: -x["contracts"])

    # NUTS / region summary
    region_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.get("nuts"):
            region_counts[r["nuts"]] += 1

    return {
        "total_buyer_winner_pairs": len(rows),
        "unique_buyer_cities": len(buyer_winners),
        "loyal_buyer_cities": loyal_buyers[:20],
        "region_distribution": dict(
            sorted(region_counts.items(), key=lambda x: -x[1])
        ),
    }


# ---------------------------------------------------------------------------
# Terminal report
# ---------------------------------------------------------------------------

def print_section(title: str):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_report(bw_results: dict, cb_results: dict, geo_results: dict):
    print_section("GRAPH 1: BUYER ↔ WINNER RELATIONSHIPS")
    s = bw_results.get("summary", {})
    print(f"  Nodes: {s.get('nodes', 0)}   Edges: {s.get('edges', 0)}")

    if "warning" in bw_results:
        print(f"  ⚠  {bw_results['warning']}")
    else:
        comps = bw_results.get("components", {})
        print(f"  Connected components: {comps.get('count', '?')}  "
              f"(largest: {comps.get('largest_size', '?')} nodes)")

        print("\n  Top 10 by PageRank (most important nodes):")
        print(f"  {'Rank':<5} {'IČO':<12} {'Type':<8} {'Value (EUR)':>14}  Name")
        print(f"  {'-'*5} {'-'*12} {'-'*8} {'-'*14}  {'-'*30}")
        for i, node in enumerate(bw_results.get("top_pagerank", [])[:10], 1):
            print(
                f"  {i:<5} {node['ico']:<12} {node['type']:<8} "
                f"{node['total_value']:>14,.0f}  {node['name'][:40]}"
            )

        print("\n  Top 10 buyer→winner relationships (by contract count):")
        print(f"  {'N':<4} {'Contracts':>9} {'Value (EUR)':>14}  Buyer → Winner")
        print(f"  {'-'*4} {'-'*9} {'-'*14}  {'-'*50}")
        for i, edge in enumerate(bw_results.get("top_edges", [])[:10], 1):
            label = f"{edge['buyer_name'][:22]} → {edge['winner_name'][:22]}"
            print(f"  {i:<4} {edge['contracts']:>9} {edge['total_value']:>14,.0f}  {label}")

        sd = bw_results.get("self_dealing", [])
        if sd:
            print(f"\n  ⚠  Self-dealing risk — entities that are BOTH buyer AND winner ({len(sd)}):")
            for x in sd[:10]:
                print(f"     {x['ico']}  {x['name'][:45]}  (value: {x['total_value']:,.0f} EUR)")
        else:
            print("\n  No self-dealing patterns detected.")

    print_section("GRAPH 2: CO-BIDDING ANALYSIS")
    s = cb_results.get("summary", {})
    print(f"  Nodes: {s.get('nodes', 0)}   Edges: {s.get('edges', 0)}")
    print(f"  Community detection: {cb_results.get('community_method', '?')}")

    if "warning" in cb_results:
        print(f"  ⚠  {cb_results['warning']}")
    else:
        comms = cb_results.get("communities", {})
        print(f"  Communities found: {comms.get('count', '?')}  "
              f"(largest: {comms.get('largest_size', '?')} members)")

        pairs = cb_results.get("top_cobidding_pairs", [])[:10]
        if pairs:
            print("\n  Top 10 co-bidding pairs:")
            print(f"  {'Co-bids':>7} {'A wins':>7} {'B wins':>7}  Pair")
            print(f"  {'-'*7} {'-'*7} {'-'*7}  {'-'*50}")
            for p in pairs:
                label = f"{p['company_a_name'][:24]} ↔ {p['company_b_name'][:24]}"
                print(f"  {p['co_bids']:>7} {p['a_wins']:>7} {p['b_wins']:>7}  {label}")

        signals = cb_results.get("bid_rigging_signals", [])
        if signals:
            print(f"\n  ⚠  Bid-rigging signals — one-sided winning ({len(signals)} pairs):")
            for s in signals[:10]:
                print(
                    f"     {s['co_bids']} co-bids, winner always: "
                    f"{s['consistent_winner_name'][:40]}  "
                    f"(IČO {s['consistent_winner_ico']})"
                )
        else:
            print("\n  No one-sided co-bidding patterns found.")

    print_section("GRAPH 3: GEOGRAPHIC PATTERNS")
    print(f"  Buyer↔winner pairs with city data: {geo_results.get('total_buyer_winner_pairs', 0)}")
    print(f"  Unique buyer cities: {geo_results.get('unique_buyer_cities', 0)}")

    loyal = geo_results.get("loyal_buyer_cities", [])
    if loyal:
        print(f"\n  Cities where ONE winner takes ALL contracts (≥2 contracts):")
        print(f"  {'Contracts':>9} {'Value (EUR)':>14}  City → Winner")
        print(f"  {'-'*9} {'-'*14}  {'-'*50}")
        for x in loyal[:10]:
            label = f"{x['buyer_mesto']} → {x['winner_name'][:35]}"
            print(f"  {x['contracts']:>9} {x['total_value']:>14,.0f}  {label}")
    else:
        print("\n  No single-winner city patterns found.")

    reg = geo_results.get("region_distribution", {})
    if reg:
        print("\n  Contract distribution by region (NUTS):")
        for region, count in list(reg.items())[:8]:
            print(f"     {count:>4}  {region}")


# ---------------------------------------------------------------------------
# Save outputs
# ---------------------------------------------------------------------------

def save_outputs(
    bw_results: dict,
    cb_results: dict,
    geo_results: dict,
    G_bw: nx.DiGraph,
    G_cb: nx.Graph,
    out_dir: Path,
):
    out_dir.mkdir(parents=True, exist_ok=True)

    # JSON report
    report = {
        "buyer_winner_graph": {
            "summary": bw_results.get("summary"),
            "top_20_pagerank": bw_results.get("top_pagerank", []),
            "top_20_edges": bw_results.get("top_edges", []),
            "components": bw_results.get("components"),
            "self_dealing_risk": bw_results.get("self_dealing", []),
        },
        "cobidding_graph": {
            "summary": cb_results.get("summary"),
            "community_method": cb_results.get("community_method"),
            "communities": cb_results.get("communities"),
            "bid_rigging_signals": cb_results.get("bid_rigging_signals", []),
            "top_20_cobidding_pairs": cb_results.get("top_cobidding_pairs", []),
        },
        "geographic_analysis": geo_results,
    }

    json_path = out_dir / "graph_analysis.json"
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(f"\n  Saved JSON report:  {json_path}")

    # GraphML — buyer↔winner
    if G_bw.number_of_nodes() > 0:
        bw_path = out_dir / "buyer_winner_graph.graphml"
        # GraphML requires string/numeric attributes — ensure types
        for _, data in G_bw.nodes(data=True):
            for k, v in data.items():
                if v is None:
                    data[k] = ""
        for _, _, data in G_bw.edges(data=True):
            for k, v in data.items():
                if v is None:
                    data[k] = 0
        nx.write_graphml(G_bw, str(bw_path))
        print(f"  Saved GraphML:      {bw_path}")

    # GraphML — co-bidding
    if G_cb.number_of_nodes() > 0:
        cb_path = out_dir / "cobidding_graph.graphml"
        for _, data in G_cb.nodes(data=True):
            for k, v in data.items():
                if v is None:
                    data[k] = ""
        for _, _, data in G_cb.edges(data=True):
            for k, v in data.items():
                if v is None:
                    data[k] = 0
        nx.write_graphml(G_cb, str(cb_path))
        print(f"  Saved GraphML:      {cb_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build and analyse procurement relationship graphs from UVO data."
    )
    parser.add_argument(
        "--db", default=str(DB_PATH),
        help=f"Path to SQLite DB (default: {DB_PATH})"
    )
    parser.add_argument(
        "--json-glob", default=JSON_GLOB,
        help="Glob pattern for parser JSON files (used when building DB)"
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Force rebuild of SQLite DB from JSON files even if DB exists"
    )
    parser.add_argument(
        "--out-dir", default=str(RESULTS_DIR),
        help=f"Directory for output files (default: {RESULTS_DIR})"
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    out_dir = Path(args.out_dir)

    # ---- Ensure DB exists ----
    if not db_path.exists() or args.rebuild:
        action = "Rebuilding" if db_path.exists() else "Building"
        print(f"\n{action} SQLite database at {db_path} …")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if db_path.exists():
            db_path.unlink()
        count = build_db(db_path, args.json_glob)
        if count == 0:
            print("ERROR: No data loaded. Cannot build graphs.")
            sys.exit(1)
    else:
        print(f"\nUsing existing DB: {db_path}")

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    # Quick sanity counts
    total_docs = con.execute("SELECT COUNT(*) FROM dokumenty").fetchone()[0]
    total_vysledky = con.execute("SELECT COUNT(*) FROM dokumenty WHERE action='vysledok'").fetchone()[0]
    total_ucastnici = con.execute("SELECT COUNT(*) FROM ucastnici").fetchone()[0]
    print(f"  DB: {total_docs} documents, {total_vysledky} results, {total_ucastnici} participants")

    # Minimal data check
    if total_vysledky == 0:
        print("\nWARNING: No 'vysledok' documents in DB — graphs will be empty.")
        print("Make sure parser results include result announcements.")

    # ---- Load data ----
    company_names = load_company_names(con)
    company_cities = load_company_cities(con)
    bw_pairs = load_buyer_winner_pairs(con)
    tenders_by_doc = load_all_bidders_per_tender(con)

    print(f"  Buyer↔winner pairs: {len(bw_pairs)}")
    print(f"  Tenders with ≥2 identified bidders: {len(tenders_by_doc)}")

    # ---- Build graphs ----
    print("\nBuilding graphs …")
    G_bw = build_buyer_winner_graph(bw_pairs, company_names, company_cities)
    G_cb = build_cobidding_graph(tenders_by_doc, company_names)
    print(f"  Buyer↔winner graph: {G_bw.number_of_nodes()} nodes, {G_bw.number_of_edges()} edges")
    print(f"  Co-bidding graph:   {G_cb.number_of_nodes()} nodes, {G_cb.number_of_edges()} edges")

    # ---- Analyse ----
    print("\nRunning analysis …")
    bw_results = analyse_buyer_winner(G_bw, company_names)
    cb_results = analyse_cobidding(G_cb, company_names)
    geo_results = analyse_geography(con, company_names)
    con.close()

    # ---- Report ----
    print_report(bw_results, cb_results, geo_results)

    # ---- Save ----
    print_section("SAVING OUTPUTS")
    save_outputs(bw_results, cb_results, geo_results, G_bw, G_cb, out_dir)

    print()


if __name__ == "__main__":
    main()
