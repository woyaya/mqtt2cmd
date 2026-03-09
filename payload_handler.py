"""
Payload handler and command executor module
Handles payload validation and command execution
"""

import json
import subprocess
import logging
import os
import pwd
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed


class PayloadHandler:
    """Handle payload validation and command execution"""
    
    def __init__(self, logger: logging.Logger):
        """
        Initialize payload handler
        
        Args:
            logger: Logger instance
        """
        self.logger = logger
    
    def validate_payload(self, payload: str, expected_type: str, expected_content: Any) -> bool:
        """
        Validate payload against expected type and content
        
        Args:
            payload: Received payload string
            expected_type: Expected payload type ('string' or 'json')
            expected_content: Expected payload content
            
        Returns:
            True if payload matches, False otherwise
        """
        try:
            if expected_type == 'string':
                return payload == expected_content
            
            elif expected_type == 'json':
                try:
                    payload_data = json.loads(payload)
                    return self._match_json(payload_data, expected_content)
                except json.JSONDecodeError as e:
                    self.logger.error(f"Invalid JSON payload: {e}")
                    return False
            
            else:
                self.logger.error(f"Unknown payload type: {expected_type}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error validating payload: {e}")
            return False
    
    def _match_json(self, payload_data: Any, expected_data: Any) -> bool:
        """
        Match JSON payload with expected data
        
        Args:
            payload_data: Parsed payload data
            expected_data: Expected data structure
            
        Returns:
            True if data matches, False otherwise
        """
        if isinstance(expected_data, dict) and isinstance(payload_data, dict):
            for key, value in expected_data.items():
                if key not in payload_data:
                    return False
                if not self._match_json(payload_data[key], value):
                    return False
            return True
        else:
            return payload_data == expected_data
    
    def execute_commands(
        self,
        commands: List[str],
        execution_mode: str = 'sequential',
        ignore_errors: bool = False,
        working_dir: str = None,
        env_vars: Dict[str, str] = None,
        run_as_user: str = None
    ) -> bool:
        """
        Execute list of commands
        
        Args:
            commands: List of shell commands to execute
            execution_mode: 'sequential' or 'parallel'
            ignore_errors: Whether to continue on error (sequential mode only)
            working_dir: Working directory for command execution
            env_vars: Additional environment variables
            run_as_user: Username to run commands as (requires root)
            
        Returns:
            True if all commands succeeded, False otherwise
        """
        if execution_mode == 'sequential':
            return self._execute_sequential(commands, ignore_errors, working_dir, env_vars, run_as_user)
        elif execution_mode == 'parallel':
            return self._execute_parallel(commands, working_dir, env_vars, run_as_user)
        else:
            self.logger.error(f"Unknown execution mode: {execution_mode}")
            return False
    
    def _execute_sequential(self, commands: List[str], ignore_errors: bool, working_dir: str = None, env_vars: Dict[str, str] = None, run_as_user: str = None) -> bool:
        """
        Execute commands sequentially
        
        Args:
            commands: List of commands
            ignore_errors: Whether to continue on error
            working_dir: Working directory
            env_vars: Environment variables
            run_as_user: Username to run commands as
            
        Returns:
            True if all commands succeeded (or errors ignored), False otherwise
        """
        all_success = True
        
        for idx, command in enumerate(commands):
            self.logger.info(f"Executing command {idx + 1}/{len(commands)}: {command}")
            
            success = self._execute_single_command(command, working_dir, env_vars, run_as_user)
            
            if not success:
                all_success = False
                if not ignore_errors:
                    self.logger.error(f"Command failed, stopping execution: {command}")
                    return False
                else:
                    self.logger.warning(f"Command failed but continuing (ignore_errors=True): {command}")
        
        return all_success
    
    def _execute_parallel(self, commands: List[str], working_dir: str = None, env_vars: Dict[str, str] = None, run_as_user: str = None) -> bool:
        """
        Execute commands in parallel
        
        Args:
            commands: List of commands
            working_dir: Working directory
            env_vars: Environment variables
            run_as_user: Username to run commands as
            
        Returns:
            True if all commands succeeded, False otherwise
        """
        all_success = True
        
        with ThreadPoolExecutor(max_workers=len(commands)) as executor:
            future_to_command = {
                executor.submit(self._execute_single_command, cmd, working_dir, env_vars, run_as_user): cmd
                for cmd in commands
            }
            
            for future in as_completed(future_to_command):
                command = future_to_command[future]
                try:
                    success = future.result()
                    if not success:
                        all_success = False
                except Exception as e:
                    self.logger.error(f"Exception executing command '{command}': {e}")
                    all_success = False
        
        return all_success
    
    def _execute_single_command(self, command: str, working_dir: str = None, env_vars: Dict[str, str] = None, run_as_user: str = None) -> bool:
        """
        Execute a single shell command
        
        Args:
            command: Shell command to execute
            working_dir: Working directory for execution
            env_vars: Additional environment variables
            run_as_user: Username to run command as (requires root)
            
        Returns:
            True if command succeeded, False otherwise
        """
        try:
            # Prepare environment
            env = os.environ.copy()
            if env_vars:
                env.update(env_vars)
            
            # Log working directory if specified
            if working_dir:
                self.logger.debug(f"Working directory: {working_dir}")
            
            # If run_as_user is specified, wrap command with sudo
            if run_as_user:
                current_uid = os.getuid()
                if current_uid == 0:  # Running as root
                    # Get user info
                    try:
                        user_info = pwd.getpwnam(run_as_user)
                        self.logger.debug(f"Running command as user: {run_as_user} (uid: {user_info.pw_uid})")
                        
                        # Use sudo to run as specified user
                        # -u: user, -H: set HOME, -i: simulate initial login
                        command = f"sudo -u {run_as_user} -H bash -c {subprocess.list2cmdline([command])}"
                    except KeyError:
                        self.logger.error(f"User '{run_as_user}' does not exist")
                        return False
                else:
                    # Not running as root, this should have been caught in validation
                    self.logger.error(f"Cannot run as user '{run_as_user}': not running as root")
                    return False
            
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes timeout
                cwd=working_dir,
                env=env
            )
            
            if result.returncode == 0:
                self.logger.info(f"Command succeeded: {command}")
                if result.stdout:
                    self.logger.debug(f"Command output: {result.stdout.strip()}")
                return True
            else:
                self.logger.error(f"Command failed with code {result.returncode}: {command}")
                if result.stderr:
                    self.logger.error(f"Error output: {result.stderr.strip()}")
                return False
                
        except subprocess.TimeoutExpired:
            self.logger.error(f"Command timed out: {command}")
            return False
        except Exception as e:
            self.logger.error(f"Error executing command '{command}': {e}")
            return False
