import logging

logger = logging.getLogger(__name__)


def on_boot(context):
    from .. import api

    api.enable()
    logger.info("[OIDC-AUTH] Login provider registered")
    return context


def on_disable(context):
    from .. import api

    api.disable()
    logger.info("[OIDC-AUTH] Login provider unregistered")
    return context
