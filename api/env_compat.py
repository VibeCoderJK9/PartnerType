"""Old environment variable names keep working for existing installations.

The project used to be called PartnerHub, and instances created then carry
PARTNERHUB_* variables in their generated compose files. Every variable is now
PARTNERTYPE_*. On import of the `api` package, each old variable whose new name
is unset is copied to the new name, and one warning line lists the old names so
an operator knows to rename them. The new name always wins when both are set.
"""
import logging
import os

OLD_PREFIX = "PARTNERHUB_"
NEW_PREFIX = "PARTNERTYPE_"

logger = logging.getLogger("partnertype.config")


def apply_legacy_names(environ=None):
    """Copy PARTNERHUB_X to PARTNERTYPE_X where needed; return the old names seen."""
    environ = os.environ if environ is None else environ
    legacy = sorted(name for name in environ if name.startswith(OLD_PREFIX))
    for name in legacy:
        environ.setdefault(NEW_PREFIX + name[len(OLD_PREFIX):], environ[name])
    if legacy:
        logger.warning("deprecated environment variables %s: rename them to %s*",
                       ", ".join(legacy), NEW_PREFIX)
    return legacy
