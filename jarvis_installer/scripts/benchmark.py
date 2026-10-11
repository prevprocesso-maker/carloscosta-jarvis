#!/usr/bin/env python3
"""Benchmark script for JARVIS performance testing."""

from __future__ import annotations

import asyncio
import sys
import time
import statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from jarvis_installer.paths import get_jarvis_paths
from jarvis_installer.core.config import load_config
from jarvis_installer.bootstrap import create_application
from jarvis_installer.core.session import SessionManager


async def benchmark_text_processing(iterations: int = 10) -> dict:
    """Benchmark text processing latency."""
    print(f"[Benchmark] Text processing ({iterations} iterations)...")
    
    paths = get_jarvis_paths()
    config = load_config(get_jarvis_paths().config_file)
    
    app = create_application()
    app.initialize_brains()
    session_mgr = SessionManager()
    
    times = []
    test_inputs = [
        "que horas são",
        "abre o bloco de notas",
        "qual o clima em São Paulo",
        "pesquisa sobre inteligência artificial",
        "lembra de comprar leite",
    ]
    
    for i in range(iterations):
        test_input = test_inputs[i % len(test_inputs)]
        session = session_mgr.get_or_create("benchmark", "test", f"bench_{i}")
        
        start = time.perf_counter()
        async for _ in app.brain_factory.get("fast").process(session, text_input=test_input):
            pass
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
        
        if (i + 1) % 5 == 0:
            print(f"  Iteration {i+1}: {elapsed:.1f}ms")
    
    return {
        "iterations": iterations,
        "avg_ms": statistics.mean(times),
        "median_ms": statistics.median(times),
        "p95_ms": sorted(times)[int(len(times) * 0.95)],
        "min_ms": min(times),
        "max_ms": max(times),
        "stdev_ms": statistics.stdev(times) if len(times) > 1 else 0,
    }


async def benchmark_voice_latency(iterations: int = 5) -> dict:
    """Benchmark voice pipeline latency (requires audio hardware)."""
    print(f"[Benchmark] Voice latency ({iterations} iterations)...")
    print("[yellow]Voice benchmark requires audio hardware - skipping[/yellow]")
    return {"skipped": True, "reason": "Requires audio hardware"}


async def benchmark_memory_usage() -> dict:
    """Benchmark memory usage."""
    import psutil
    process = psutil.Process()
    
    # Baseline
    baseline = process.memory_info().rss / 1024 / 1024
    
    # Load app
    paths = get_jarvis_paths()
    config = load_config(get_jarvis_paths().config_file)
    
    app = create_application()
    app.initialize_brains()
    
    after_load = process.memory_info().rss / 1024 / 1024
    
    # Run some operations
    session_mgr = SessionManager()
    for i in range(10):
        session = session_mgr.get_or_create("memtest", "test", f"mem_{i}")
        async for _ in app.brain_factory.get("fast").process(session, text_input="test"):
            pass
    
    after_ops = process.memory_info().rss / 1024 / 1024
    
    return {
        "baseline_mb": round(baseline, 1),
        "after_load_mb": round(after_load, 1),
        "after_ops_mb": round(after_ops, 1),
        "load_overhead_mb": round(after_load - baseline, 1),
        "ops_overhead_mb": round(after_ops - after_load, 1),
    }


async def run_all_benchmarks():
    print("=" * 60)
    print("JARVIS Benchmark Suite")
    print("=" * 60)
    
    results = {}
    
    # Text processing
    results["text_processing"] = await benchmark_text_processing(20)
    
    # Voice (if available)
    results["voice"] = await benchmark_voice_latency(5)
    
    # Memory
    try:
        results["memory"] = await benchmark_memory_usage()
    except ImportError:
        results["memory"] = {"skipped": True, "reason": "psutil not available"}
    
    # Summary
    print("\n" + "=" * 60)
    print("BENCHMARK RESULTS")
    print("=" * 60)
    
    tp = results["text_processing"]
    print(f"\nText Processing ({tp['iterations']} iterations):")
    print(f"  Average:    {tp['avg_ms']:.1f} ms")
    print(f"  Median:     {tp['median_ms']:.1f} ms")
    print(f"  P95:        {tp['p95_ms']:.1f} ms")
    print(f"  Min:        {tp['min_ms']:.1f} ms")
    print(f"  Max:        {tp['max_ms']:.1f} ms")
    print(f"  StdDev:     {tp['stdev_ms']:.1f} ms")
    
    if "memory" in results and "skipped" not in results["memory"]:
        mem = results["memory"]
        print(f"\nMemory Usage:")
        print(f"  Baseline:      {mem['baseline_mb']:.1f} MB")
        print(f"  After Load:    {mem['after_load_mb']:.1f} MB")
        print(f"  After Ops:     {mem['after_ops_mb']:.1f} MB")
        print(f"  Load Overhead: {mem['load_overhead_mb']:.1f} MB")
    
    return results


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-iterations", type=int, default=20)
    parser.add_argument("--voice-iterations", type=int, default=5)
    parser.add_argument("--output", type=str, help="Output JSON file")
    args = parser.parse_args()
    
    results = await run_all_benchmarks()
    
    if args.output:
        import json
        Path(args.output).write_text(json.dumps(results, indent=2))
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())