"""Constants for the Solaire Cloud integration."""

DOMAIN = "solaire_cloud"

CONF_SHARE_CODE = "share_code"
CONF_AES_KEY = "aes_key"
CONF_AES_IV = "aes_iv"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 60  # seconds
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 600

# Consecutive rejected responses before Home Assistant asks for a new share link.
REJECTS_BEFORE_REAUTH = 3
MAX_BACKOFF = 600  # seconds
