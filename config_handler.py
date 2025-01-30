import json
import os
import logging

logger = logging.getLogger(__name__)

CONFIG_FILE = 'config.json'

DEFAULT_CONFIG = {
    "ENDPOINT_URL": "",
    "DEPLOYMENT_NAME": "",
    "AZURE_OPENAI_API_KEY": "",
    "API_VERSION": "",
    "MIN_MESSAGES_FOR_ANALYSIS": 5,
    "MAX_MESSAGES_FOR_ANALYSIS": 20,
    "fix_time": 5
}


def load_config():
    """Load configuration from config.json file"""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # Ensure all required fields exist
                for key in DEFAULT_CONFIG:
                    if key not in config:
                        config[key] = DEFAULT_CONFIG[key]
                return config
        else:
            logger.info(f"Configuration file {CONFIG_FILE} not found, creating with default values")
            save_config(DEFAULT_CONFIG)
            return DEFAULT_CONFIG
    except Exception as e:
        logger.error(f"Error loading configuration: {str(e)}")
        return DEFAULT_CONFIG


def save_config(config):
    """Save configuration to config.json file"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
        logger.info(f"Configuration saved to {CONFIG_FILE}")
        return True
    except Exception as e:
        logger.error(f"Error saving configuration: {str(e)}")
        return False


def update_config(new_config):
    """Update configuration with new values"""
    try:
        current_config = load_config()
        current_config.update(new_config)
        save_config(current_config)
        return True
    except Exception as e:
        logger.error(f"Error updating configuration: {str(e)}")
        return False
