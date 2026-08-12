import os
import sys
import importlib
from pathlib import Path

MONOREPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKSPACE_ROOT = MONOREPO_ROOT.parent

PATH_IT = WORKSPACE_ROOT / "master" / "it_indeed cwgeopy" / "Auto_indeed"
PATH_GEN = WORKSPACE_ROOT / "master" / "gen_indeed" / "Auto_indeed"

# Cache of imported master modules to avoid redundant disk lookups and re-imports
_MODULE_CACHE = {}

def get_master_dir(bot_name: str | None = None) -> Path:
    if not bot_name:
        bot_name = os.environ.get("BOT_NAME", "indeed_general")
    
    name_lower = bot_name.lower()
    if "it" in name_lower:
        return PATH_IT
    else:
        return PATH_GEN

def get_module(module_name: str, bot_name: str | None = None):
    """
    Returns the module object loaded from the appropriate master folder bot.
    """
    if not bot_name:
        bot_name = os.environ.get("BOT_NAME", "indeed_general")
    
    target_dir = get_master_dir(bot_name)
    cache_key = (target_dir, module_name)
    if cache_key in _MODULE_CACHE:
        return _MODULE_CACHE[cache_key]

    # Save original sys.path and sys.modules keys to avoid side effects
    orig_path = list(sys.path)
    
    # Prepend target_dir to sys.path
    sys.path.insert(0, str(target_dir))
    
    # Evict conflicting modules from sys.modules
    conflicting = ["modules", "core", "config", "runAiBot"]
    saved_modules = {}
    for m in list(sys.modules):
        if any(m == c or m.startswith(c + ".") for c in conflicting):
            saved_modules[m] = sys.modules.pop(m)
            
    try:
        # Import the requested module
        mod = importlib.import_module(module_name)
        _MODULE_CACHE[cache_key] = mod
        return mod
    finally:
        # Restore sys.path
        sys.path = orig_path
        
        # Restore sys.modules so the monorepo's own environment isn't corrupted
        for m in list(sys.modules):
            if any(m == c or m.startswith(c + ".") for c in conflicting):
                sys.modules.pop(m, None)
        sys.modules.update(saved_modules)
