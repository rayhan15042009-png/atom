"""
Atom Frame Profiler — quick run, intro disabled.
Patches overlay.py methods and measures per-frame timing for 8 seconds.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Patch methods BEFORE importing atom
from ui import overlay as ov_module

_timing = {}
_original_methods = {}

def _make_wrapper(method_name):
    """Factory: creates a timing wrapper for method_name."""
    orig = getattr(ov_module.AtomOverlay, method_name)
    _original_methods[method_name] = orig
    
    def wrapper(self, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            result = orig(self, *args, **kwargs)
        except Exception as e:
            result = None
        elapsed = time.perf_counter() - t0
        _timing.setdefault(method_name, []).append(elapsed)
        return result
    return wrapper

# Patch
for fn in ['paintEvent', '_tick_update', '_tick_animation', '_draw_sprite']:
    setattr(ov_module.AtomOverlay, fn, _make_wrapper(fn))
    print(f"[Profiler] Patched {fn}")

import atom
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

DURATION = 8

def _print_summary():
    elapsed = DURATION
    print(f"\n{'='*70}")
    print(f"  ATOM FRAME PROFILER — {elapsed}s run")
    print(f"{'='*70}")
    print(f"  {'Method':<28} {'Calls':>7} {'Total':>10} {'Avg':>10} {'Max':>10}")
    print(f"  {'-'*28} {'-'*7} {'-'*10} {'-'*10} {'-'*10}")
    
    all_total_ms = 0
    for name, samples in sorted(_timing.items(), key=lambda x: -sum(x[1])):
        n = len(samples)
        total = sum(samples) * 1000
        avg = total / n if n else 0
        mx = max(samples) * 1000 if samples else 0
        print(f"  {name:<28} {n:>7} {total:>8.2f}ms {avg:>8.3f}ms {mx:>8.3f}ms")
        all_total_ms += total
    
    paint_count = len(_timing.get('paintEvent', []))
    frame_cost = all_total_ms / max(1, paint_count) if paint_count else 0
    max_fps = 1000.0 / max(0.001, frame_cost) if frame_cost > 0 else 0
    
    print(f"  {'-'*28} {'-'*7} {'-'*10} {'-'*10} {'-'*10}")
    print(f"  Total methods timed: {len(_timing)}")
    print(f"  paintEvent frames: {paint_count}")
    print(f"  Avg frame cost: {frame_cost:.3f}ms")
    print(f"  Theoretical max FPS: {max_fps:.0f}")
    print(f"{'='*70}\n")
    
    # Save to file for later reference
    try:
        (ROOT / 'logs' / 'profiler_data.txt').write_text(
            f"Atom Frame Profiler - {elapsed}s run\n"
            f"{'='*60}\n", encoding='utf-8')
    except:
        pass
    
    QApplication.quit()

QTimer.singleShot(DURATION * 1000, _print_summary)
print(f"[Profiler] Running for {DURATION}s...")
sys.exit(atom.main())
