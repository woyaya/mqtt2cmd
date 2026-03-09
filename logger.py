"""
Logging module with rotation support
Handles log file creation, rotation, and cleanup
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


class LogManager:
    """Manage application logging with rotation and cleanup"""
    
    def __init__(self, log_file: str, log_level: str = 'INFO', retention_days: int = 7):
        """
        Initialize log manager
        
        Args:
            log_file: Path to log file
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            retention_days: Number of days to keep old log files
        """
        self.log_file = log_file
        self.log_level = self._parse_log_level(log_level)
        self.retention_days = retention_days
        self.logger = None
        
    def _parse_log_level(self, level: str) -> int:
        """Convert string log level to logging constant"""
        levels = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL
        }
        return levels.get(level.upper(), logging.INFO)
    
    def setup_logger(self, name: str = 'mqtt_subscriber') -> logging.Logger:
        """
        Setup and configure logger
        
        Args:
            name: Logger name
            
        Returns:
            Configured logger instance
        """
        # Create logger
        self.logger = logging.getLogger(name)
        self.logger.setLevel(self.log_level)
        
        # Remove existing handlers
        self.logger.handlers.clear()
        
        # Create log directory if it doesn't exist
        log_dir = os.path.dirname(self.log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # Create file handler with daily rotation
        file_handler = TimedRotatingFileHandler(
            self.log_file,
            when='midnight',
            interval=1,
            backupCount=self.retention_days,
            encoding='utf-8'
        )
        file_handler.setLevel(self.log_level)
        
        # Create console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(self.log_level)
        
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        
        # Add handlers to logger
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        
        return self.logger
    
    def cleanup_old_logs(self) -> None:
        """Remove log files older than retention period"""
        if not os.path.exists(self.log_file):
            return
        
        log_dir = os.path.dirname(self.log_file) or '.'
        log_basename = os.path.basename(self.log_file)
        
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        
        try:
            for filename in os.listdir(log_dir):
                if filename.startswith(log_basename):
                    file_path = os.path.join(log_dir, filename)
                    
                    # Skip the current log file
                    if file_path == self.log_file:
                        continue
                    
                    # Check file modification time
                    file_mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                    
                    if file_mtime < cutoff_date:
                        os.remove(file_path)
                        if self.logger:
                            self.logger.info(f"Removed old log file: {filename}")
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error cleaning up old logs: {e}")
    
    def get_logger(self) -> Optional[logging.Logger]:
        """Get the configured logger instance"""
        return self.logger
