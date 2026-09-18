#!/usr/bin/env python3
"""
Benchmark Ollama models on analyst's Q&A files.
Each file contains a prompt + document — measures response time and quality.

Usage:
  python3 tools/ollama-qa-benchmark.py
  python3 tools/ollama-qa-benchmark.py qwen2.5:32b
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
MODELS = sys.argv[1:] if len(sys.argv) > 1 else ["qwen2.5:7b", "qwen2.5:32b"]
QA_DIR = Path(__file__).parent.parent / "data" / "CFE-Test" / "Otazky LLM Robo"


def call_ollama(model: str, prompt: str) -> dict:
    start = time.time()
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return {"response": "", "time": time.time() - start, "error": str(e)}

    elapsed = time.time() - start
    response = data.get("response", "")
    tokens = data.get("eval_count", 0)
    prompt_tokens = data.get("prompt_eval_count", 0)
    tok_per_sec = tokens / (data.get("eval_duration", 1) / 1e9) if data.get("eval_duration") else 0

    return {
        "response": response,
        "time": elapsed,
        "prompt_tokens": prompt_tokens,
        "output_tokens": tokens,
        "tok_per_sec": round(tok_per_sec, 1),
    }


def main():
    files = sorted(QA_DIR.glob("*.txt"))
    if not files:
        print(f"❌ Žiadne súbory v {QA_DIR}")
        sys.exit(1)

    print("=" * 70)
    print("OLLAMA Q&A BENCHMARK")
    print(f"Súborov: {len(files)}")
    print(f"Modely: {', '.join(MODELS)}")
    print("=" * 70)

    results = []

    for f in files:
        prompt = f.read_text(encoding="utf-8", errors="ignore").strip()
        name = f.stem
        # Detect question type from filename
        q_type = "klasifikácia (A-H)" if name.startswith("q1") else "extrakcia účastníkov"
        chars = len(prompt)
        print(f"\n{'─' * 70}")
        print(f"📄 {name}")
        print(f"   Typ: {q_type} | {chars} znakov (~{chars // 4} tokenov)")

        file_results = {"file": name, "type": q_type, "chars": chars, "models": {}}

        for model in MODELS:
            print(f"\n   🤖 {model}:", end=" ", flush=True)
            r = call_ollama(model, prompt)

            if r.get("error"):
                print(f"❌ {r['error']}")
                file_results["models"][model] = {"error": r["error"]}
                continue

            response = r["response"].strip()
            # Truncate display
            display = response[:200] + ("..." if len(response) > 200 else "")
            print(f"{r['time']:.1f}s | {r['prompt_tokens']} in + {r['output_tokens']} out | {r['tok_per_sec']} tok/s")
            print(f"   📝 {display}")

            file_results["models"][model] = {
                "time": round(r["time"], 1),
                "prompt_tokens": r["prompt_tokens"],
                "output_tokens": r["output_tokens"],
                "tok_per_sec": r["tok_per_sec"],
                "response": response,
            }

        results.append(file_results)

    # Summary table
    print(f"\n{'=' * 70}")
    print("SÚHRN")
    print(f"{'=' * 70}")
    print(f"\n{'Súbor':<45} ", end="")
    for m in MODELS:
        print(f"{'│ ' + m:<25}", end="")
    print()
    print("─" * (45 + 25 * len(MODELS)))

    for r in results:
        name = r["file"][:44]
        print(f"{name:<45} ", end="")
        for m in MODELS:
            mr = r["models"].get(m, {})
            if "error" in mr:
                print(f"│ {'❌ error':<23}", end="")
            else:
                t = mr.get("time", 0)
                tok = mr.get("tok_per_sec", 0)
                print(f"│ {t:>5.1f}s  {tok:>5.1f} tok/s   ", end="")
        print()

    # Total times
    print("─" * (45 + 25 * len(MODELS)))
    print(f"{'CELKOM':<45} ", end="")
    for m in MODELS:
        total = sum(r["models"].get(m, {}).get("time", 0) for r in results)
        print(f"│ {total:>5.1f}s               ", end="")
    print()

    # Save
    out_file = QA_DIR / "benchmark_results.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nUložené: {out_file}")


if __name__ == "__main__":
    main()
