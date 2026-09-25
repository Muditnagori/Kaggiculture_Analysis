# SPDX-License-Identifier: Apache-2.0
"""
Loads a submission's main.py (and its route_compressor.py / route_db/
dependency) as a fresh Python module, from any directory, without needing
to copy files into this project or edit the submission itself.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def load_submission(submission_dir: str | Path) -> types.ModuleType:
    """
    submission_dir must contain main.py, route_compressor.py, and route_db/
    (the same layout as the .tar.gz submission archive). Returns the loaded
    main module, freshly imported under a unique name so repeated calls
    don't collide with each other's module-level route-selection state.
    """
    submission_dir = Path(submission_dir).resolve()
    main_path = submission_dir / "main.py"
    if not main_path.exists():
        raise FileNotFoundError(f"no main.py found in {submission_dir}")
    if not (submission_dir / "route_compressor.py").exists():
        raise FileNotFoundError(f"no route_compressor.py found in {submission_dir} (main.py needs it)")
    if not (submission_dir / "route_db").exists():
        raise FileNotFoundError(f"no route_db/ found in {submission_dir} (main.py needs it)")

    # main.py imports route_compressor by bare name and route_db by relative
    # path resolved from its own __file__ -- both need submission_dir on
    # sys.path / as the working context for the load to succeed.
    if str(submission_dir) not in sys.path:
        sys.path.insert(0, str(submission_dir))

    # Unique module name per load so instrumenting one loaded copy's globals
    # (module._current_route_id etc.) never bleeds into another.
    module_name = f"field_skeleton_submission_{id(submission_dir)}_{len(sys.modules)}"
    spec = importlib.util.spec_from_file_location(module_name, main_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build import spec for {main_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
