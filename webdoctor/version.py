"""Single source of truth for the WebDoctor version.

``pyproject.toml`` reads this value via setuptools dynamic versioning, and
``webdoctor.config`` reads it when package metadata is unavailable (e.g. when
running directly from a source checkout).
"""

__version__ = "1.0.0"
