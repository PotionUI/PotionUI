import sys
import os
import importlib

# Add the comfyui-backend plugin directory to sys.path so `from backend.xxx`
# resolves to this plugin's backend package.
#
# IMPORTANT: Other plugin tests (e.g. downloader) may have already imported a
# different 'backend' package at collection time. We need to:
# 1. Remove stale 'backend' entries from sys.modules
# 2. Ensure our path is first in sys.path
# 3. Re-import the 'backend' package from our path

plugin_dir = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'content', 'plugins', 'marketplace', 'comfyui-backend'
))

# Remove any previously cached 'backend' and all its submodules
for key in list(sys.modules.keys()):
    if key == 'backend' or key.startswith('backend.'):
        del sys.modules[key]

# Ensure our plugin dir is at the very front of sys.path
if plugin_dir in sys.path:
    sys.path.remove(plugin_dir)
sys.path.insert(0, plugin_dir)

# Invalidate import caches and pre-import backend to claim the namespace
importlib.invalidate_caches()
import backend  # noqa: F401 - ensures backend resolves to our plugin
