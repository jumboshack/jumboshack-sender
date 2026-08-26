"""Sender version, stamped into every reading's metadata as `software_version`
so Jumboshack can show which sender version each device is running. Override at
runtime with the GEO_AGENT_VERSION env var (e.g. to stamp a git sha at deploy time)."""

__version__ = "1.1.0"
