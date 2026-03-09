"""
Configuration file parser module
Handles YAML configuration parsing and validation
"""

import yaml
import os
import pwd
from typing import Dict, Any, Optional
from pathlib import Path


class ConfigParser:
    """Parse and validate YAML configuration files"""
    
    def __init__(self, config_path: str):
        """
        Initialize configuration parser
        
        Args:
            config_path: Path to main configuration file
        """
        self.config_path = config_path
        self.password_file = None
        self.config = None
        self.passwords = {}
        self.current_user = os.getenv('USER') or os.getenv('USERNAME') or 'unknown'
        self.current_uid = os.getuid()
        self.is_root = (self.current_uid == 0)
        
    def load_passwords(self) -> Dict[str, str]:
        """Load passwords from password configuration file"""
        if not self.password_file or not os.path.exists(self.password_file):
            return {}
            
        try:
            with open(self.password_file, 'r', encoding='utf-8') as f:
                password_config = yaml.safe_load(f)
                return password_config.get('passwords', {})
        except Exception as e:
            raise ValueError(f"Failed to load password file: {e}")
    
    def load_config(self) -> Dict[str, Any]:
        """Load and parse main configuration file"""
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
        
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML format: {e}")
        
        # Get password file path from global config
        global_config = self.config.get('global', {})
        self.password_file = global_config.get('password_file')
        
        # Load passwords if password file is specified
        self.passwords = self.load_passwords()
        
        # Replace password references with actual passwords
        self._resolve_passwords(self.config)
        
        return self.config
    
    def _resolve_passwords(self, config: Any) -> None:
        """Recursively resolve password references in configuration"""
        if isinstance(config, dict):
            for key, value in config.items():
                if key == 'password' and isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                    # Extract password key from ${key} format
                    password_key = value[2:-1]
                    if password_key in self.passwords:
                        config[key] = self.passwords[password_key]
                else:
                    self._resolve_passwords(value)
        elif isinstance(config, list):
            for item in config:
                self._resolve_passwords(item)
    
    def get_global_config(self) -> Dict[str, Any]:
        """Get global configuration section"""
        if not self.config:
            raise ValueError("Configuration not loaded. Call load_config() first.")
        return self.config.get('global', {})
    
    def get_mqtt_servers(self) -> Dict[str, Any]:
        """Get all MQTT server configurations with global defaults applied"""
        if not self.config:
            raise ValueError("Configuration not loaded. Call load_config() first.")
        
        mqtt_servers = self.config.get('mqtt_servers', {})
        global_config = self.config.get('global', {})
        
        # Apply global defaults to each server
        for server_name, server_config in mqtt_servers.items():
            mqtt_servers[server_name] = self._apply_global_defaults(server_config, global_config)
        
        return mqtt_servers
    
    def _apply_global_defaults(self, server_config: Dict[str, Any], global_config: Dict[str, Any]) -> Dict[str, Any]:
        """Apply global defaults to server configuration"""
        # List of keys that can have global defaults
        inheritable_keys = [
            'username', 'password', 'client_id', 'keepalive', 'use_tls',
            'execution_mode', 'ignore_errors', 'working_dir', 'env_vars',
            'max_reconnect_delay', 'run_as_user'
        ]
        
        # Apply global defaults if not specified in server config
        for key in inheritable_keys:
            if key not in server_config and key in global_config:
                server_config[key] = global_config[key]
        
        # Apply defaults to subscriptions
        if 'subscriptions' in server_config:
            for topic, topic_config in server_config['subscriptions'].items():
                server_config['subscriptions'][topic] = self._apply_subscription_defaults(
                    topic_config, server_config, global_config
                )
        
        return server_config
    
    def _apply_subscription_defaults(
        self,
        topic_config: Dict[str, Any],
        server_config: Dict[str, Any],
        global_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply defaults to subscription configuration"""
        # Apply defaults to handlers
        if 'handlers' in topic_config:
            for idx, handler in enumerate(topic_config['handlers']):
                topic_config['handlers'][idx] = self._apply_handler_defaults(
                    handler, server_config, global_config
                )
        
        return topic_config
    
    def _apply_handler_defaults(
        self,
        handler: Dict[str, Any],
        server_config: Dict[str, Any],
        global_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Apply defaults to handler configuration"""
        # Keys that can be inherited
        inheritable_keys = ['execution_mode', 'ignore_errors', 'working_dir', 'env_vars', 'run_as_user']
        
        for key in inheritable_keys:
            if key not in handler:
                # Try server config first, then global config
                if key in server_config:
                    handler[key] = server_config[key]
                elif key in global_config:
                    handler[key] = global_config[key]
        
        # Set final defaults if still not specified
        if 'execution_mode' not in handler:
            handler['execution_mode'] = 'sequential'
        if 'ignore_errors' not in handler:
            handler['ignore_errors'] = False
        
        return handler
    def validate_config(self) -> bool:
        """Validate configuration structure and required fields"""
        if not self.config:
            raise ValueError("Configuration not loaded. Call load_config() first.")
        
        # Validate global section
        global_config = self.config.get('global', {})
        if 'log_level' not in global_config:
            raise ValueError("Missing required field: global.log_level")
        
        # Validate run_as_user in global config
        if 'run_as_user' in global_config:
            self._validate_run_as_user(global_config['run_as_user'], 'global')
        
        # Validate MQTT servers
        mqtt_servers = self.config.get('mqtt_servers', {})
        if not mqtt_servers:
            raise ValueError("No MQTT servers configured")
        
        for server_name, server_config in mqtt_servers.items():
            self._validate_server_config(server_name, server_config)
        
        return True
    
    def _validate_server_config(self, server_name: str, server_config: Dict[str, Any]) -> None:
        """Validate individual MQTT server configuration"""
        required_fields = ['host', 'port', 'username']
        for field in required_fields:
            if field not in server_config:
                raise ValueError(f"Missing required field in server '{server_name}': {field}")
        
        # Validate subscriptions
        subscriptions = server_config.get('subscriptions', {})
        for topic, topic_config in subscriptions.items():
            self._validate_topic_config(server_name, topic, topic_config)
    
    def _validate_topic_config(self, server_name: str, topic: str, topic_config: Dict[str, Any]) -> None:
        """Validate topic configuration"""
        if 'qos' not in topic_config:
            raise ValueError(f"Missing QoS in server '{server_name}', topic '{topic}'")
        
        handlers = topic_config.get('handlers', [])
        if not handlers:
            raise ValueError(f"No handlers defined for server '{server_name}', topic '{topic}'")
        
        for idx, handler in enumerate(handlers):
            if 'payload_type' not in handler:
                raise ValueError(f"Missing payload_type in handler {idx} for topic '{topic}'")
            # payload field is optional for json type when using variables
            if 'payload' not in handler and handler.get('payload_type') == 'string':
                raise ValueError(f"Missing payload in handler {idx} for topic '{topic}'")
            if 'commands' not in handler:
                raise ValueError(f"Missing commands in handler {idx} for topic '{topic}'")
            
            # Validate run_as_user in handler
            if 'run_as_user' in handler:
                self._validate_run_as_user(handler['run_as_user'], f"server '{server_name}', topic '{topic}', handler {idx}")
    
    def _validate_run_as_user(self, username: str, location: str) -> None:
        """
        Validate run_as_user configuration
        
        Args:
            username: Username to run commands as
            location: Configuration location for error messages
        
        Raises:
            ValueError: If user doesn't exist or permission denied
        """
        # Check if user exists
        try:
            pwd.getpwnam(username)
        except KeyError:
            raise ValueError(f"User '{username}' does not exist (configured in {location})")
        
        # If not running as root and trying to use different user
        if not self.is_root and username != self.current_user:
            raise ValueError(
                f"Permission denied: Cannot run commands as user '{username}' "
                f"(current user: '{self.current_user}', configured in {location}). "
                f"Only root can run commands as other users."
            )