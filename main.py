"""
Main application entry point
MQTT subscriber with command execution
"""

import sys
import time
import argparse
import os
import pwd
import threading
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
        self.handler_semaphore = None  # Will be initialized after config load
        self.active_handlers = 0
        self.handler_lock = threading.Lock()
        
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
            
            # Initialize handler semaphore for concurrency control
            max_concurrent = global_config.get('max_concurrent_handlers', 20)
            self.handler_semaphore = threading.Semaphore(max_concurrent)
            self.logger.info(f"Handler concurrency limit: {max_concurrent}")
            
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
                
                # Create payload handler with global config
                global_config = self.config.get('global', {})
                payload_handler = PayloadHandler(self.logger, global_config)
                
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
                        # Create a thread to handle message asynchronously
                        handler_thread = threading.Thread(
                            target=self._handle_message_async,
                            args=(payload_handler, topic, payload, hc),
                            daemon=True,
                            name=f"handler-{topic}-{time.time()}"
                        )
                        handler_thread.start()
                    return handler
                
                mqtt_client.register_handler(topic, create_handler(handler_config))
    
    def _handle_message_async(
        self,
        payload_handler: PayloadHandler,
        topic: str,
        payload: str,
        handler_config: Dict[str, Any]
    ) -> None:
        """
        Handle received MQTT message asynchronously with concurrency control
        
        Args:
            payload_handler: Payload handler instance
            topic: MQTT topic
            payload: Message payload
            handler_config: Handler configuration
        """
        # Acquire semaphore (wait if at limit)
        self.handler_semaphore.acquire()
        
        with self.handler_lock:
            self.active_handlers += 1
            self.logger.debug(f"Active handlers: {self.active_handlers}")
        
        try:
            self._handle_message(payload_handler, topic, payload, handler_config)
        finally:
            with self.handler_lock:
                self.active_handlers -= 1
            self.handler_semaphore.release()
    
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
            
            # Get execution parameters
            execution_mode = handler_config.get('execution_mode', 'sequential')
            ignore_errors = handler_config.get('ignore_errors', False)
            working_dir = handler_config.get('working_dir')
            env_vars = handler_config.get('env_vars', {})
            commands = handler_config['commands']
            
            # First pass: resolve env_vars and working_dir without additional env_vars
            # This allows env_vars to reference YAML and PAYLOAD variables
            resolver_pass1 = VariableResolver(yaml_vars, parsed_payload, payload_type)
            if working_dir:
                working_dir = resolver_pass1.resolve(working_dir, escape=False)
            if env_vars:
                env_vars = resolver_pass1.resolve_dict(env_vars, escape=False)
            
            # Execute commands
            success = payload_handler.execute_commands(
                commands,
                execution_mode,
                ignore_errors,
                working_dir,
                env_vars,
                yaml_vars,
                parsed_payload,
                payload_type
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
    
    # Load config early to check run_as_user
    try:
        config_parser = ConfigParser(args.config)
        config_parser.load_config()
        config_parser.validate_config()
        global_config = config_parser.get_global_config()
        run_as_user = global_config.get('run_as_user')
        
        # Handle user switching if configured
        if run_as_user:
            current_user = pwd.getpwuid(os.getuid()).pw_name
            current_uid = os.getuid()
            
            if current_user == run_as_user:
                # Same user, no need to switch
                print(f"Running as configured user: {run_as_user}")
            elif current_uid == 0:
                # Running as root, switch to target user
                try:
                    user_info = pwd.getpwnam(run_as_user)
                    os.setgid(user_info.pw_gid)
                    os.setuid(user_info.pw_uid)
                    os.environ['HOME'] = user_info.pw_dir
                    os.environ['USER'] = run_as_user
                    os.environ['LOGNAME'] = run_as_user
                    print(f"Switched from root to user: {run_as_user} (uid: {user_info.pw_uid})")
                except KeyError:
                    print(f"ERROR: User '{run_as_user}' does not exist")
                    sys.exit(1)
                except Exception as e:
                    print(f"ERROR: Failed to switch to user '{run_as_user}': {e}")
                    sys.exit(1)
            else:
                # Not root and different user - error
                print(f"ERROR: Cannot switch to user '{run_as_user}'")
                print(f"Current user: {current_user} (uid: {current_uid})")
                print(f"Target user: {run_as_user}")
                print(f"Solution: Run as root or run as user '{run_as_user}' directly")
                sys.exit(1)
    
    except Exception as e:
        print(f"ERROR: Failed to load configuration: {e}")
        sys.exit(1)
    
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
