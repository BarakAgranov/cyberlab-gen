"""Registries subpackage — bundled + overlay registry loading and merging.

Implements the bundled (read-only) + user-overlay (writable) hierarchy
per ``docs/schema.md §4.11``, with overlay-wins semantics. The loader
validates bundled files through ``BundledRegistryFile[E]`` and overlay
files through ``OverlayRegistryFile[E]`` so the structural guarantee
(``schema-details.md §6.6``: a bundled file containing ``proposals:``
fails Layer 1) is preserved.

Public surface re-exported here per the project's cross-subpackage
import convention; intra-subpackage modules import from siblings
directly.
"""

from cyberlab_gen.errors import RegistryError, RegistryLoadError
from cyberlab_gen.registries.loader import (
    REGISTRY_FILE_NAMES,
    LoadedRegistryLayer,
    bundled_registry_dir,
    default_overlay_dir,
    load_bundled,
    load_bundled_file,
    load_mitre_techniques,
    load_overlay,
    load_overlay_file,
    load_static_catalogs,
)
from cyberlab_gen.registries.merge import (
    MergedRegistries,
    load_merged_registries,
    merge_layers,
)

__all__ = [
    "REGISTRY_FILE_NAMES",
    "LoadedRegistryLayer",
    "MergedRegistries",
    "RegistryError",
    "RegistryLoadError",
    "bundled_registry_dir",
    "default_overlay_dir",
    "load_bundled",
    "load_bundled_file",
    "load_merged_registries",
    "load_mitre_techniques",
    "load_overlay",
    "load_overlay_file",
    "load_static_catalogs",
    "merge_layers",
]
