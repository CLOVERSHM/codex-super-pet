"""Load optional local adapters only from the private data directory."""
import importlib.util
from pathlib import Path


def load_extension(store, path, source=None):
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        return None
    resolved = path.resolve()
    if not resolved.is_relative_to(store.path.parent.resolve()):
        raise ValueError("私有扩展路径越界")
    spec = importlib.util.spec_from_file_location("_delivery_private_extension", resolved)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Extension(store, source)
