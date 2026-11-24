"""Default configuration for WebDoctor."""
import os

DEFAULT_TIMEOUT = int(os.environ.get("WEBDOCTOR_TIMEOUT", "20"))
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 "
    "WebDoctor/1.0"
)
MAX_LINKS_TO_CHECK = 40
LINK_CHECK_CONCURRENCY = 8
MAX_PAGE_WAIT_MS = 25000
AI_MODEL_FAST = "gpt-6-luna"
AI_MODEL_DEEP = "gpt-6.1-sol"
TOOL_VERSION = "1.0.0"
