import logging
import sys
from app.core.config import settings

def setup_logging():
    """Configure logging for the application."""
    # Create formatter
    formatter = logging.Formatter(settings.LOG_FORMAT)
    
    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.LOG_LEVEL)
    root_logger.addHandler(console_handler)
    
    # Configure specific loggers
    loggers = {
        "translation": logging.getLogger("translation"),
        "document": logging.getLogger("document"),
        "api": logging.getLogger("api"),
        "worker": logging.getLogger("worker")
    }
    
    for logger in loggers.values():
        logger.setLevel(settings.LOG_LEVEL)
        logger.addHandler(console_handler)
    
    return loggers

# Initialize logging
loggers = setup_logging() 