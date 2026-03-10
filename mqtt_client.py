"""
MQTT client module
Handles MQTT connection, subscription, and message handling
"""

import paho.mqtt.client as mqtt
import json
import logging
import time
import threading
from typing import Dict, Any, Callable, Optional


class MQTTClientManager:
    """Manage MQTT client connections and subscriptions"""
    
    def __init__(self, server_name: str, server_config: Dict[str, Any], logger: logging.Logger):
        """
        Initialize MQTT client manager
        
        Args:
            server_name: Name of the MQTT server
            server_config: MQTT server configuration
            logger: Logger instance
        """
        self.server_name = server_name
        self.server_config = server_config
        self.logger = logger
        self.client = None
        self.message_handlers = {}
        self.is_connected = False
        self.reconnect_delay = 1
        self.max_reconnect_delay = server_config.get('max_reconnect_delay', 60)
        self.should_reconnect = True
        self.reconnect_thread = None
        self.connection_timeout = server_config.get('connection_timeout', 10)
        self.connection_monitor_thread = None
        
    def setup_client(self) -> mqtt.Client:
        """Setup and configure MQTT client"""
        client_id = self.server_config.get('client_id', '')
        
        # Create MQTT client
        self.client = mqtt.Client(client_id=client_id)
        
        # Set username and password
        username = self.server_config.get('username')
        password = self.server_config.get('password', '')
        if username:
            self.client.username_pw_set(username, password)
        
        # Set callbacks
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        
        # Set TLS if configured
        if self.server_config.get('use_tls', False):
            self.client.tls_set()
        
        return self.client
    
    def connect(self) -> bool:
        """
        Connect to MQTT broker with exponential backoff retry
        
        Returns:
            True if connection initiated, False otherwise
        """
        try:
            host = self.server_config['host']
            port = self.server_config['port']
            keepalive = self.server_config.get('keepalive', 60)
            
            self.logger.info(f"[{self.server_name}] Attempting to connect to MQTT broker {host}:{port}")
            self.client.connect_async(host, port, keepalive)
            
            # Start a thread to monitor connection timeout
            self._start_connection_monitor()
            
            return True
        except Exception as e:
            self.logger.error(f"[{self.server_name}] Failed to initiate connection to MQTT broker: {e}")
            if self.should_reconnect:
                self._schedule_reconnect()
            return False
    
    def _schedule_reconnect(self) -> None:
        """Schedule reconnection with exponential backoff"""
        if self.reconnect_thread and self.reconnect_thread.is_alive():
            return  # Reconnection already scheduled
        
        self.logger.warning(f"[{self.server_name}] Scheduling reconnection with {self.reconnect_delay}s delay")
        self.reconnect_thread = threading.Thread(target=self._reconnect_with_backoff)
        self.reconnect_thread.daemon = True
        self.reconnect_thread.start()
    
    def _start_connection_monitor(self) -> None:
        """Start a thread to monitor connection timeout"""
        if self.connection_monitor_thread and self.connection_monitor_thread.is_alive():
            return  # Monitor already running
        
        self.connection_monitor_thread = threading.Thread(target=self._monitor_connection_timeout)
        self.connection_monitor_thread.daemon = True
        self.connection_monitor_thread.start()
    
    def _monitor_connection_timeout(self) -> None:
        """Monitor connection timeout and trigger reconnection if needed"""
        time.sleep(self.connection_timeout)
        
        if not self.is_connected and self.should_reconnect:
            self.logger.error(f"[{self.server_name}] Connection timeout after {self.connection_timeout} seconds")
            self._schedule_reconnect()
    
    def _reconnect_with_backoff(self) -> None:
        """Reconnect with exponential backoff delay"""
        while self.should_reconnect and not self.is_connected:
            self.logger.info(f"[{self.server_name}] Will reconnect in {self.reconnect_delay} seconds...")
            time.sleep(self.reconnect_delay)
            
            # Increase delay for next attempt (exponential backoff) BEFORE attempting
            next_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
            
            try:
                host = self.server_config['host']
                port = self.server_config['port']
                keepalive = self.server_config.get('keepalive', 60)
                
                self.logger.info(f"[{self.server_name}] Attempting to reconnect to {host}:{port}")
                
                # Use connect() instead of reconnect() to handle both initial and subsequent connections
                self.client.connect(host, port, keepalive)
                
                # If connect() succeeds without exception, connection is in progress
                # The _on_connect callback will be triggered when connection completes
                # Update delay after successful attempt initiation
                self.reconnect_delay = next_delay
                
                # Wait a bit to see if connection succeeds
                # If it fails, _on_connect will be called with error code
                break  # Exit loop, let the connection attempt proceed
                
            except Exception as e:
                self.logger.error(f"[{self.server_name}] Reconnection attempt failed: {e}")
                self.reconnect_delay = next_delay
    
    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker"""
        if rc == 0:
            self.is_connected = True
            self.reconnect_delay = 1  # Reset reconnect delay on successful connection
            self.logger.info(f"[{self.server_name}] Successfully connected to MQTT broker")
            
            # Subscribe to all configured topics
            self._subscribe_topics()
        else:
            self.is_connected = False
            error_messages = {
                1: "Connection refused - incorrect protocol version",
                2: "Connection refused - invalid client identifier",
                3: "Connection refused - server unavailable",
                4: "Connection refused - bad username or password",
                5: "Connection refused - not authorized"
            }
            error_msg = error_messages.get(rc, f"Connection refused - code {rc}")
            self.logger.error(f"[{self.server_name}] Failed to connect: {error_msg}")
            
            # Schedule reconnection on connection failure
            if self.should_reconnect:
                self._schedule_reconnect()
    
    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker"""
        self.is_connected = False
        if rc != 0:
            self.logger.warning(f"[{self.server_name}] Unexpected disconnection from MQTT broker (code: {rc})")
            # Start reconnection with exponential backoff
            if self.should_reconnect:
                self._schedule_reconnect()
        else:
            self.logger.info(f"[{self.server_name}] Disconnected from MQTT broker")
    
    def _on_message(self, client, userdata, msg):
        """Callback when message received"""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            self.logger.debug(f"[{self.server_name}] Received message on topic '{topic}': {payload}")
            
            # Find and execute matching handlers
            if topic in self.message_handlers:
                for handler in self.message_handlers[topic]:
                    handler(topic, payload)
            else:
                self.logger.warning(f"[{self.server_name}] No handler registered for topic: {topic}")
                
        except Exception as e:
            self.logger.error(f"[{self.server_name}] Error processing message: {e}")
    
    def _subscribe_topics(self) -> None:
        """Subscribe to all configured topics"""
        subscriptions = self.server_config.get('subscriptions', {})
        
        for topic, topic_config in subscriptions.items():
            qos = topic_config.get('qos', 0)
            
            try:
                result, mid = self.client.subscribe(topic, qos)
                if result == mqtt.MQTT_ERR_SUCCESS:
                    self.logger.info(f"[{self.server_name}] Subscribed to topic '{topic}' with QoS {qos}")
                else:
                    self.logger.error(f"[{self.server_name}] Failed to subscribe to topic '{topic}'")
            except Exception as e:
                self.logger.error(f"[{self.server_name}] Error subscribing to topic '{topic}': {e}")
    
    def register_handler(self, topic: str, handler: Callable) -> None:
        """
        Register message handler for a topic
        
        Args:
            topic: MQTT topic
            handler: Callback function to handle messages
        """
        if topic not in self.message_handlers:
            self.message_handlers[topic] = []
        self.message_handlers[topic].append(handler)
        self.logger.debug(f"[{self.server_name}] Registered handler for topic: {topic}")
    
    def start_loop(self) -> None:
        """Start MQTT client loop in background thread"""
        try:
            self.client.loop_start()
            self.logger.info(f"[{self.server_name}] MQTT client loop started")
        except Exception as e:
            self.logger.error(f"[{self.server_name}] Error starting MQTT loop: {e}")
    
    def stop_loop(self) -> None:
        """Stop MQTT client loop"""
        try:
            self.client.loop_stop()
            self.logger.info(f"[{self.server_name}] MQTT client loop stopped")
        except Exception as e:
            self.logger.error(f"[{self.server_name}] Error stopping MQTT loop: {e}")
    
    def disconnect(self) -> None:
        """Disconnect from MQTT broker"""
        self.should_reconnect = False
        if self.client:
            try:
                if self.is_connected:
                    self.client.disconnect()
                self.stop_loop()
                self.logger.info(f"[{self.server_name}] Disconnected from MQTT broker")
            except Exception as e:
                self.logger.error(f"[{self.server_name}] Error during disconnect: {e}")
