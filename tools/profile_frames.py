"""
Atom Frame Profiler — measures per-frame rendering costs.

Patches AtomOverlay's key methods at runtime to add timing instrumentation.
Outputs timing data to logs/atom_profile.log and prints summary at exit.

Usage:
    python tools/profile_frames.py [duration_seconds]
    
This will start atom.py with profiling enabled for the given duration (default 30s),
then print a summary of per-frame costs and exit.
"""

import atexit
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Timing storage
_timing_data = defaultdict(list)
_sample_count = 0  # counts individual method calls
_frame_count = 0   # counts paintEvent invocations (actual frames)
_start_time = None

def _log_timing(method_name: str, elapsed_s: float):
    """Record a timing sample for a method."""
    global _sample_count, _frame_count
    _timing_data[method_name].append(elapsed_s)
    _sample_count += 1
    if method_name == 'paintEvent':
        _frame_count += 1

def _install_profiler():
    """Patch AtomOverlay methods with timing instrumentation."""
    from ui import overlay as overlay_module
    
    original_methods = {}
    
    # Methods to profile with their names
    profile_targets = [
        ('paintEvent', '_paintEvent_profiled'),
        ('_tick_update', '_tick_update_profiled'),
        ('_tick_animation', '_tick_animation_profiled'),
        ('_draw_sprite', '_draw_sprite_profiled'),
        ('_draw_speech', '_draw_speech_profiled'),
    ]
    
    for method_name, wrapper_name in profile_targets:
        original = getattr(overlay_module.AtomOverlay, method_name, None)
        if original is None:
            print(f"[Profiler] Method {method_name} not found, skipping")
            continue
        original_methods[method_name] = original
        
        def make_wrapper(mname, orig):
            def wrapper(self, *args, **kwargs):
                t0 = time.perf_counter()
                result = orig(self, *args, **kwargs)
                elapsed = time.perf_counter() - t0
                _log_timing(mname, elapsed)
                return result
            return wrapper
        
        setattr(overlay_module.AtomOverlay, wrapper_name, make_wrapper(method_name, original))
        # Replace original with wrapper
        setattr(overlay_module.AtomOverlay, method_name, make_wrapper(method_name, original))
        print(f"[Profiler] Patched {method_name}")
    
    # Also profile 3D renderer if available
    try:
        from ui import renderer_3d as renderer_module
        if hasattr(renderer_module, 'BlockRenderer3D'):
            orig_paint = getattr(renderer_module.BlockRenderer3D, 'paintGL', None)
            if orig_paint:
                def make_paint_wrapper(orig):
                    def wrapper(self, *args, **kwargs):
                        t0 = time.perf_counter()
                        result = orig(self, *args, **kwargs)
                        elapsed = time.perf_counter() - t0
                        _log_timing('paintGL_3d', elapsed)
                        return result
                    return wrapper
                setattr(renderer_module.BlockRenderer3D, 'paintGL', make_paint_wrapper(orig_paint))
                print(f"[Profiler] Patched BlockRenderer3D.paintGL")
            
            orig_animate = getattr(renderer_module.BlockRenderer3D, '_animate', None)
            if orig_animate:
                def make_animate_wrapper(orig):
                    def wrapper(self, *args, **kwargs):
                        t0 = time.perf_counter()
                        result = orig(self, *args, **kwargs)
                        elapsed = time.perf_counter() - t0
                        _log_timing('_animate_3d', elapsed)
                        return result
                    return wrapper
                setattr(renderer_module.BlockRenderer3D, '_animate', make_animate_wrapper(orig_animate))
                print(f"[Profiler] Patched BlockRenderer3D._animate")
    except ImportError:
        print("[Profiler] 3D renderer not available, skipping")
    
    # Also profile 2D sprite drawing
    try:
        from ui import sprite_2d as sprite_module
        if hasattr(sprite_module, 'Sprite2D'):
            orig_draw = getattr(sprite_module.Sprite2D, 'draw', None)
            if orig_draw:
                def make_sprite_wrapper(orig):
                    def wrapper(self, painter, x, y, *args, **kwargs):
                        t0 = time.perf_counter()
                        result = orig(self, painter, x, y, *args, **kwargs)
                        elapsed = time.perf_counter() - t0
                        _log_timing('sprite_draw_2d', elapsed)
                        return result
                    return wrapper
                setattr(sprite_module.Sprite2D, 'draw', make_sprite_wrapper(orig_draw))
                print(f"[Profiler] Patched Sprite2D.draw")
    except ImportError:
        print("[Profiler] Sprite2D not available, skipping")

def _print_summary(duration_s: float):
    """Print profiling summary."""
    print()
    print("=" * 70)
    print("  ATOM FRAME PROFILER SUMMARY")
    print(f"  Duration: {duration_s:.1f}s | Total frames captured: {_frame_count}")
    print("=" * 70)
    
    if not _timing_data:
        print("  No profiling data collected.")
        print("=" * 70)
        return
    
    # Sort by total time (descending)
    sorted_methods = sorted(_timing_data.items(), key=lambda x: sum(x[1]), reverse=True)
    
    print(f"  {'Method':<30} {'Calls':>8} {'Total':>10} {'Avg':>10} {'Max':>10} {'FPS impact':>12}")
    print(f"  {'-'*30} {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")
    
    total_all = sum(sum(times) for _, times in sorted_methods)
    
    for method_name, times in sorted_methods:
        n = len(times)
        total = sum(times)
        avg = total / n if n else 0
        mx = max(times) if times else 0
        pct = (total / total_all * 100) if total_all else 0
        
        # FPS impact: if this method takes X ms per frame, max FPS = 1000/X
        fps_impact = 1000.0 / (avg * 1000) if avg > 0 else "N/A"
        fps_str = f"{fps_impact:.0f} FPS" if isinstance(fps_impact, float) else "N/A"
        
        print(f"  {method_name:<30} {n:>8} {total*1000:>8.2f}ms {avg*1000:>8.3f}ms {mx*1000:>8.3f}ms {fps_str:>12}")
    
    print(f"  {'-'*30} {'-'*8} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")
    print(f"  {'TOTAL':<30} {_frame_count:>8} {total_all*1000:>8.2f}ms")
    
    # Per-frame breakdown
    avg_frame_time = total_all / _frame_count if _frame_count else 0
    print()
    print(f"  Average frame time: {avg_frame_time*1000:.3f}ms")
    print(f"  Theoretical max FPS: {1000.0/(avg_frame_time*1000):.0f}" if avg_frame_time > 0 else "  No data")
    
    # Bottleneck analysis
    print()
    print("  BOTTLENECK ANALYSIS:")
    print(f"  {'-' * 60}")
    
    if _timing_data.get('paintEvent', []):
        avg_paint = sum(_timing_data['paintEvent']) / len(_timing_data['paintEvent'])
        paint_fps = 1000.0 / (avg_paint * 1000) if avg_paint > 0 else 0
        print(f"  QPainter (paintEvent): {avg_paint*1000:.3f}ms avg — max ~{paint_fps:.0f} FPS")
        
        if avg_paint > 0.016:
            print(f"  !! paintEvent exceeds 16ms target ({avg_paint*1000:.1f}ms vs 16ms)")
        if avg_paint > 0.033:
            print(f"  !! paintEvent exceeds 33ms target — can't sustain 30 FPS")
    
    if _timing_data.get('paintGL_3d', []):
        avg_3d = sum(_timing_data['paintGL_3d']) / len(_timing_data['paintGL_3d'])
        print(f"  OpenGL (paintGL 3D): {avg_3d*1000:.3f}ms avg")
    
    if _timing_data.get('sprite_draw_2d', []):
        avg_2d = sum(_timing_data['sprite_draw_2d']) / len(_timing_data['sprite_draw_2d'])
        print(f"  2D Sprite draw: {avg_2d*1000:.3f}ms avg")
    
    print("=" * 70)
    
    # Save detailed data
    log_path = ROOT / 'logs' / 'atom_profile.json'
    try:
        summary = {}
        for method, times in _timing_data.items():
            summary[method] = {
                'count': len(times),
                'total_ms': sum(times) * 1000,
                'avg_ms': (sum(times) / len(times)) * 1000 if times else 0,
                'max_ms': max(times) * 1000 if times else 0,
                'min_ms': min(times) * 1000 if times else 0,
            }
        (ROOT / 'logs').mkdir(exist_ok=True)
        log_path.write_text(json.dumps({
            'duration_s': duration_s,
            'total_frames': _frame_count,
            'methods': summary
        }, indent=2), encoding='utf-8')
        print(f"  Detailed data saved to {log_path}")
    except Exception as e:
        print(f"  Could not save detailed data: {e}")


def main():
    """Run atom.py with profiling enabled."""
    duration = 30  # default 30 seconds
    
    if len(sys.argv) > 1:
        try:
            duration = int(sys.argv[1])
        except ValueError:
            print(f"Usage: python {sys.argv[0]} [duration_seconds]")
            sys.exit(1)
    
    print(f"Atom Frame Profiler — running for {duration}s")
    print("=" * 70)
    
    # Install profiler patches
    _install_profiler()
    
    # Start atom.py
    import atom
    
    _start_time = time.time()
    
    # Set up shutdown timer
    from PyQt6.QtCore import QTimer
    
    def on_shutdown():
        elapsed = time.time() - _start_time
        _print_summary(elapsed)
        # Use QApplication.quit() which works regardless of variable name
        from PyQt6.QtWidgets import QApplication as _QA
        _QA.quit()
        sys.exit(0)
    
    # Schedule shutdown after duration
    QTimer.singleShot(duration * 1000, on_shutdown)
    
    # Also register atexit for early termination
    atexit.register(lambda: _print_summary(time.time() - _start_time))
    
    # Run the app
    sys.exit(atom.main())


if __name__ == '__main__':
    main()
