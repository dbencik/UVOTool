import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent.parent
TOOLS_DIR = BASE_DIR / "tools"
PIPELINES_DIR = BASE_DIR / "config" / "pipelines"
DB_PATH = BASE_DIR / "data" / "vestnik.db"


class PipelineEngine:
    def __init__(self):
        from .modules import MODULE_REGISTRY
        self.modules = MODULE_REGISTRY

    def get_modules(self) -> list[dict]:
        """Return all modules as dicts for API."""
        return [m.to_dict() for m in self.modules.values()]

    def get_module(self, module_id: str) -> dict | None:
        m = self.modules.get(module_id)
        return m.to_dict() if m else None

    def list_pipelines(self) -> list[dict]:
        """List all saved pipeline configs."""
        PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
        pipelines = []
        for f in sorted(PIPELINES_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                pipelines.append({
                    "id": f.stem,
                    "name": data.get("name", f.stem),
                    "nodes": len(data.get("nodes", [])),
                    "edges": len(data.get("edges", [])),
                })
            except Exception:
                pass
        return pipelines

    def get_pipeline(self, pipeline_id: str) -> dict | None:
        path = PIPELINES_DIR / f"{pipeline_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def save_pipeline(self, pipeline_id: str, config: dict):
        PIPELINES_DIR.mkdir(parents=True, exist_ok=True)
        path = PIPELINES_DIR / f"{pipeline_id}.json"
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False))

    def run_pipeline(self, pipeline_id: str, params: dict) -> dict:
        """Execute a pipeline. Returns results per node."""
        config = self.get_pipeline(pipeline_id)
        if not config:
            return {"error": f"Pipeline '{pipeline_id}' not found"}

        nodes = config.get("nodes", [])
        edges = config.get("edges", [])

        # Build dependency graph
        deps = {n["id"]: [] for n in nodes}
        for edge in edges:
            deps[edge["to"]].append(edge["from"])

        # Topological sort
        order = self._topo_sort(nodes, deps)

        # Execute in order, with parallel execution for independent nodes
        results = {}
        node_data = {}  # node_id -> output data

        for batch in order:
            # batch = list of node IDs that can run in parallel
            futures = {}
            with ThreadPoolExecutor(max_workers=4) as executor:
                for node_id in batch:
                    node = next(n for n in nodes if n["id"] == node_id)
                    module_id = node["module"]
                    module = self.modules.get(module_id)
                    if not module:
                        results[node_id] = {"status": "error", "message": f"Unknown module: {module_id}"}
                        continue

                    # Collect inputs from params + upstream nodes
                    node_params = dict(params)
                    for edge in edges:
                        if edge["to"] == node_id and edge["from"] in node_data:
                            mapping = edge.get("mapping", {})
                            for src_key, dst_key in mapping.items():
                                if src_key in node_data[edge["from"]]:
                                    node_params[dst_key] = node_data[edge["from"]][src_key]

                    # Apply output settings (enabled/disabled)
                    settings = node.get("settings", {})

                    futures[executor.submit(self._run_module, module_id, node_params)] = node_id

                for future in as_completed(futures):
                    nid = futures[future]
                    try:
                        result = future.result(timeout=60)
                        node_data[nid] = result.get("data", {})

                        # Filter by enabled outputs
                        node_cfg = next((n for n in nodes if n["id"] == nid), {})
                        output_settings = node_cfg.get("settings", {}).get("outputs", {})
                        if output_settings and result.get("data"):
                            filtered = {}
                            for key, val in result["data"].items():
                                setting = output_settings.get(key, {})
                                if setting.get("enabled", True):
                                    label = setting.get("label", key)
                                    filtered[key] = {"value": val, "label": label}
                            result["filtered_data"] = filtered

                        results[nid] = result
                    except Exception as e:
                        results[nid] = {"status": "error", "message": str(e)}

        return {
            "pipeline": pipeline_id,
            "params": params,
            "results": results,
            "timestamp": datetime.now().isoformat(),
        }

    def run_batch(self, pipeline_id: str, ico_list: list[str]) -> dict:
        """Run pipeline for each IČO in the list. Returns tabular results."""
        config = self.get_pipeline(pipeline_id)
        if not config:
            return {"error": f"Pipeline '{pipeline_id}' not found"}

        rows = []
        total = len(ico_list)

        for i, ico in enumerate(ico_list):
            ico = ico.strip()
            if not ico:
                continue

            result = self.run_pipeline(pipeline_id, {"ico": ico})

            # Flatten results from all nodes into one row
            row = {"ico": ico, "_index": i + 1, "_status": "success"}
            for node_id, node_result in result.get("results", {}).items():
                if node_result.get("status") == "error":
                    row["_status"] = "error"
                    continue
                data = node_result.get("data", {})
                if isinstance(data, dict):
                    for key, val in data.items():
                        if key.startswith("_") or key in ("enriched_at", "checked_at", "fetched_at"):
                            continue
                        # Prefix with module id to avoid collisions
                        node = next((n for n in config["nodes"] if n["id"] == node_id), None)
                        mod_id = node["module"] if node else node_id
                        if mod_id in ("input", "output"):
                            continue
                        # Use custom label from settings if available
                        settings = (node or {}).get("settings", {}).get("outputs", {})
                        setting = settings.get(key, {})
                        if setting.get("enabled") is False:
                            continue
                        col_name = f"{mod_id}.{key}"
                        row[col_name] = val

            rows.append(row)

        # Build column list from all rows
        columns = ["ico"]
        seen = {"ico"}
        for row in rows:
            for key in row:
                if key not in seen and not key.startswith("_"):
                    columns.append(key)
                    seen.add(key)

        return {
            "pipeline": pipeline_id,
            "total": total,
            "processed": len(rows),
            "columns": columns,
            "rows": rows,
            "timestamp": datetime.now().isoformat(),
        }

    def _run_module(self, module_id: str, params: dict) -> dict:
        """Run a single module. Returns {status, data, cached, duration_ms}."""
        start = time.time()

        # Input/Output modules just pass through params
        if module_id in ("input", "output"):
            return {"status": "success", "data": dict(params), "duration_ms": 0, "cached": False}

        # UVO/TED: read from DB when queried by IČO (not from parser)
        ico = params.get("ico", "")
        if module_id in ("uvo", "ted") and ico:
            data = self._read_uvo_ted_from_db(module_id, ico)
            elapsed = (time.time() - start) * 1000
            return {"status": "success", "data": data, "duration_ms": round(elapsed), "cached": True}

        # Analyze/Graph: read existing results, don't re-run
        if module_id in ("analyze", "graph"):
            data = self._read_module_result(module_id, ico)
            elapsed = (time.time() - start) * 1000
            return {"status": "success", "data": data, "duration_ms": round(elapsed), "cached": True}

        module = self.modules[module_id]
        script_map = {
            "orsf": "company-enrichment.py",
            "ruz": "company-enrichment.py",
            "rpvs": "rpvs-lookup.py",
            "fs_dlznici": "fs-dlznici-lookup.py",
            "sp_dlznici": "sp-dlznici-lookup.py",
            "uvo": "vestnik-parser.py",
            "ted": "ted-parser.py",
            "analyze": "vestnik-analyze.py",
            "graph": "vestnik-graph.py",
            "watchdog": "vestnik-watchdog.py",
        }

        script = script_map.get(module_id)
        if not script:
            return {"status": "error", "message": f"No script for module {module_id}"}

        script_path = TOOLS_DIR / script
        if not script_path.exists():
            return {"status": "error", "message": f"Script not found: {script_path}"}

        # Build CLI args based on module type
        args = [sys.executable, str(script_path)]
        ico = params.get("ico", "")

        if module_id in ("orsf", "ruz", "rpvs", "fs_dlznici", "sp_dlznici") and ico:
            args.append(ico)
        elif module_id == "uvo" and params.get("vestnik"):
            args.append(params["vestnik"])
        elif module_id == "ted":
            if params.get("country"):
                args.extend(["--country", params["country"]])
            if params.get("year"):
                args.append(params["year"])
        elif module_id == "watchdog":
            args.append("--all")

        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=60)
            elapsed = (time.time() - start) * 1000

            # Read results from DB or JSON
            data = self._read_module_result(module_id, ico)

            return {
                "status": "success" if result.returncode == 0 else "warning",
                "data": data,
                "duration_ms": round(elapsed),
                "cached": elapsed < 100,  # fast = was cached
            }
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "data": {}, "duration_ms": 60000}
        except Exception as e:
            return {"status": "error", "message": str(e), "data": {}}

    def _read_uvo_ted_from_db(self, module_id: str, ico: str) -> dict:
        """Read UVO/TED participation data for an IČO from DB."""
        import sqlite3
        if not DB_PATH.exists():
            return {"zakazky": 0}
        db = sqlite3.connect(str(DB_PATH))
        db.row_factory = sqlite3.Row
        try:
            # Get all tenders where this IČO participated
            rows = db.execute("""
                SELECT u.nazov, u.cena, u.je_vitaz, u.poradie, z.predmet, z.cpv_kod, z.druh,
                       o.nazov as obstaravatel, o.ico as obst_ico, d.rok, d.url, d.action
                FROM ucastnici u
                JOIN dokumenty d ON u.doc_id = d.id
                JOIN zakazky z ON u.doc_id = z.doc_id
                LEFT JOIN obstaravatelia o ON u.doc_id = o.doc_id
                WHERE u.ico = ?
                ORDER BY d.rok DESC, u.cena DESC LIMIT 50
            """, (ico,)).fetchall()

            zakazky = []
            for r in rows:
                zakazky.append({
                    "predmet": r["predmet"] or "", "obstaravatel": r["obstaravatel"] or "",
                    "hodnota": r["cena"], "je_vitaz": bool(r["je_vitaz"]),
                    "poradie": r["poradie"], "rok": r["rok"], "cpv": r["cpv_kod"] or "",
                    "url": r["url"] or ""
                })

            wins = sum(1 for z in zakazky if z["je_vitaz"])
            total_val = sum(z["hodnota"] or 0 for z in zakazky if z["je_vitaz"])

            return {
                "ucasti_celkom": len(zakazky),
                "vyhry": wins,
                "celkova_hodnota_vyhier": total_val,
                "zakazky": zakazky
            }
        except Exception:
            return {"zakazky": 0}
        finally:
            db.close()

    def _read_module_result(self, module_id: str, ico: str) -> dict:
        """Read module output from DB after execution."""
        import sqlite3

        if not DB_PATH.exists():
            return {}

        db = sqlite3.connect(str(DB_PATH))
        db.row_factory = sqlite3.Row

        try:
            if module_id == "orsf" and ico:
                row = db.execute("SELECT * FROM firmy WHERE ico = ?", (ico,)).fetchone()
                if row:
                    d = dict(row)
                    return {k: v for k, v in d.items() if k != "enriched_at"}

            elif module_id == "ruz" and ico:
                row = db.execute(
                    "SELECT trzby_posledne, trzby_predosle, zisk_posledne, zisk_predosle, rok_zavierky "
                    "FROM firmy WHERE ico = ?", (ico,)
                ).fetchone()
                if row:
                    return dict(row)

            elif module_id == "rpvs" and ico:
                rows = db.execute("SELECT * FROM rpvs WHERE ico = ?", (ico,)).fetchall()
                if rows:
                    ubos = []
                    for r in rows:
                        if r["ubo_meno"]:
                            ubos.append({
                                "meno": r["ubo_meno"],
                                "priezvisko": r["ubo_priezvisko"],
                                "datum_narodenia": r["ubo_datum_narodenia"] or "",
                            })
                    return {
                        "is_registered": bool(ubos),
                        "ubos": ubos,
                        "obchodne_meno": rows[0]["obchodne_meno"] or "",
                    }

            elif module_id == "fs_dlznici" and ico:
                row = db.execute("SELECT * FROM fs_dlznici WHERE ico = ?", (ico,)).fetchone()
                if row:
                    return dict(row)

            elif module_id == "sp_dlznici" and ico:
                row = db.execute("SELECT * FROM sp_dlznici WHERE ico = ?", (ico,)).fetchone()
                if row:
                    return dict(row)

            elif module_id == "analyze":
                path = BASE_DIR / "data" / "results" / "analysis_report.json"
                if path.exists():
                    return json.loads(path.read_text())

            elif module_id == "graph":
                path = BASE_DIR / "data" / "results" / "graph_analysis.json"
                if path.exists():
                    return json.loads(path.read_text())
        except Exception:
            pass
        finally:
            db.close()

        return {}

    def _topo_sort(self, nodes, deps):
        """Topological sort returning batches of parallel-executable nodes."""
        remaining = set(n["id"] for n in nodes)
        completed = set()
        batches = []

        while remaining:
            # Find nodes whose dependencies are all completed
            batch = [n for n in remaining if all(d in completed for d in deps.get(n, []))]
            if not batch:
                # Circular dependency -- just run remaining
                batch = list(remaining)
            batches.append(batch)
            completed.update(batch)
            remaining -= set(batch)

        return batches
