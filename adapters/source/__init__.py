"""Application adapters for explicit, governed source acquisition."""

from .git import GitSourceImportError, import_git_source

__all__ = ["GitSourceImportError", "import_git_source"]
