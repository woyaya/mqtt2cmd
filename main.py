"""
Main application entry point
MQTT subscriber with command execution
"""

import sys
import time
import argparse
from typing import Dict, Any
from config_parser import ConfigParser
from logger import LogManager
from mqtt_client import MQTTClientManager
from payload_handler import PayloadHandler
from variable_resolver import VariableResolver


class MQTTSubscriberApp:
    """Main application class"""
    
    def __init__(self, config_file: str):
        """
        Initialize application
        
        Args:
            config_file: Path to configuration file
        """
        self.config_file = config_file
        self.config = None
        self.logger = None
        self.mqtt_clients = []
        self.running = True
        
    def initialize(self) -> bool:
        """
        Initialize application components
        
        Returns:
            True if initialization successful, False otherwise
        """
        try:
            # Load configuration
            parser = ConfigParser(self.config_file)
            parser.load_config()
            parser.validate_config()
            
            # Get configuration with global defaults applied
            self.config = {
                'global': parser.get_global_config(),
                'mqtt_servers': parser.get_mqtt_servers()
            }
            
            # Setup logging
            global_config = self.config['global']
            log_file = global_config.get('log_file', 'logs/mqtt_subscriber.log')
            log_level = global_config.get('log_level', 'INFO')
            retention_days = global_config.get('log_retention_days', 7)
            
            log_manager = LogManager(log_file, log_level, retention_days)
            self.logger = log_manager.setup_logger()
            
            # Cleanup old logs
            log_manager.cleanup_old_logs()
            
            self.logger.info("Application initialized successfully")
            return True
            
        except Exception as e:
            if self.logger:
                self.logger.error(f"Initialization failed: {e}")
            else:
                print(f"Initialization failed: {e}")
            return False
    
    def setup_mqtt_clients(self) -> bool:
        """
        Setup MQTT clients for all configured servers
        
        Returns:
            True if setup successful, False otherwise
        """
        try:
            mqtt_servers = self.config.get('mqtt_servers', {})
            
            for server_name, server_config in mqtt_servers.items():
                self.logger.info(f"Setting up MQTT client for server: {server_name}")
                
                # Create MQTT client
                mqtt_client = MQTTClientManager(server_name, server_config, self.logger)
                mqtt_client.setup_client()
                
                # Create payload handler
                payload_handler = PayloadHandler(self.logger)
                
                # Register handlers for each subscription
                self._register_handlers(mqtt_client, payload_handler, server_config)
                
                # Connect to broker and start loop
                mqtt_client.connect()
                mqtt_client.start_loop()
                self.mqtt_clients.append(mqtt_client)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error setting up MQTT clients: {e}")
            return False
    
    def _register_handlers(
        self,
        mqtt_client: MQTTClientManager,
        payload_handler: PayloadHandler,
        server_config: Dict[str, Any]
    ) -> None:
        """
        Register message handlers for subscriptions
        
        Args:
            mqtt_client: MQTT client instance
            payload_handler: Payload handler instance
            server_config: Server configuration
        """
        subscriptions = server_config.get('subscriptions', {})
        
        for topic, topic_config in subscriptions.items():
            handlers = topic_config.get('handlers', [])
            
            for handler_config in handlers:
                # Create closure to capture handler configuration
                def create_handler(hc):
                    def handler(topic, payload):
                        self._handle_message(payload_handler, topic, payload, hc)
                    return handler
                
                mqtt_client.register_handler(topic, create_handler(handler_config))
    
    def _handle_message(
        self,
        payload_handler: PayloadHandler,
        topic: str,
        payload: str,
        handler_config: Dict[str, Any]
    ) -> None:
        """
        Handle received MQTT message
        
        Args:
            payload_handler: Payload handler instance
            topic: MQTT topic
            payload: Message payload
            handler_config: Handler configuration
        """
        try:
            payload_type = handler_config['payload_type']
            expected_payload = handler_config.get('payload')  # Optional for json type
            
            # Validate payload if expected_payload is specified
            if expected_payload is not None:
                if not payload_handler.validate_payload(payload, payload_type, expected_payload):
                    self.logger.warning(f"Payload validation failed for topic '{topic}'")
                    return
                self.logger.info(f"Payload validated successfully for topic '{topic}'")
            else:
                self.logger.debug(f"No payload validation configured for topic '{topic}'")
            
            # Parse payload for variable resolution
            parsed_payload = payload
            if payload_type == 'json':
                import json
                try:
                    parsed_payload = json.loads(payload)
                except json.JSONDecodeError:
                    parsed_payload = payload
            
            # Create variable resolver with YAML variables from config
            yaml_vars = self.config.get('global', {}).get('variables', {})
            resolver = VariableResolver(yaml_vars, parsed_payload, payload_type)
            
            # Resolve variables in commands
            commands = handler_config['commands']
            resolved_commands = [resolver.resolve(cmd) for cmd in commands]
            
            # Get execution parameters
            execution_mode = handler_config.get('execution_mode', 'sequential')
            ignore_errors = handler_config.get('ignore_errors', False)
            working_dir = handler_config.get('working_dir')
            env_vars = handler_config.get('env_vars', {})
            run_as_user = handler_config.get('run_as_user')
            
            # Resolve variables in working_dir and env_vars
            if working_dir:
                working_dir = resolver.resolve(working_dir, escape=False)
            if env_vars:
                env_vars = resolver.resolve_dict(env_vars, escape=False)
            
            # Execute commands
            success = payload_handler.execute_commands(
                resolved_commands,
                execution_mode,
                ignore_errors,
                working_dir,
                env_vars,
                run_as_user
            )
            
            if success:
                self.logger.info(f"All commands executed successfully for topic '{topic}'")
            else:
                self.logger.error(f"Some commands failed for topic '{topic}'")
                
        except Exception as e:
            self.logger.error(f"Error handling message for topic '{topic}': {e}")
    
    def run(self) -> None:
        """Run the application"""
        try:
            self.logger.info("Starting MQTT subscriber application")
            
            if not self.mqtt_clients:
                self.logger.error("No MQTT clients available")
                return
            
            self.logger.info(f"Running with {len(self.mqtt_clients)} MQTT server(s)")
            
            # Keep main thread alive
            while self.running:
                time.sleep(1)
                
        except KeyboardInterrupt:
            self.logger.info("Application stopped by user")
        except Exception as e:
            self.logger.error(f"Application error: {e}")
        finally:
            self.cleanup()
    
    def cleanup(self) -> None:
        """Cleanup resources"""
        self.running = False
        self.logger.info("Cleaning up resources")
        for client in self.mqtt_clients:
            client.disconnect()
        time.sleep(1)  # Give time for cleanup


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='MQTT Subscriber with Command Execution')
    parser.add_argument(
        '-c', '--config',
        default='./config.yaml',
        help='Path to configuration file (default: ./config.yaml)'
    )
    
    args = parser.parse_args()
    
    # Create and run application
    app = MQTTSubscriberApp(args.config)
    
    if app.initialize():
        if app.setup_mqtt_clients():
            app.run()
        else:
            print("Failed to setup MQTT clients")
            sys.exit(1)
    else:
        print("Failed to initialize application")
        sys.exit(1)


if __name__ == '__main__':
    main()
