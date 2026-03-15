"""
Payload handler and command executor module
Handles payload validation and command execution
"""

import json
import subprocess
import logging
import os
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from variable_resolver import VariableResolver


class PayloadHandler:
    """Handle payload validation and command execution"""
    
    def __init__(self, logger: logging.Logger, global_config: Dict[str, Any] = None):
        """
        Initialize payload handler
        
        Args:
            logger: Logger instance
            global_config: Global configuration
        """
        self.logger = logger
        self.global_config = global_config or {}
        self.max_output_size = self._parse_size(self.global_config.get('max_exec_output_size', '5M'))
        self.command_timeout = self.global_config.get('command_timeout', 0)  # 0 = disabled
    
    def _parse_size(self, size_value: Any) -> int:
        """
        Parse size value with optional K/M suffix
        
        Args:
            size_value: Size value (int, or string with K/M suffix)
            
        Returns:
            Size in bytes
        """
        if isinstance(size_value, int):
            return size_value
        
        if isinstance(size_value, str):
            size_value = size_value.strip().upper()
            
            if size_value.endswith('K'):
                return int(size_value[:-1]) * 1024
            elif size_value.endswith('M'):
                return int(size_value[:-1]) * 1024 * 1024
            else:
                return int(size_value)
        
        # Default to 5MB if invalid
        return 5 * 1024 * 1024
    
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
        yaml_vars: Dict[str, Any] = None,
        payload: Any = None,
        payload_type: str = None
    ) -> bool:
        """
        Execute list of commands
        
        Args:
            commands: List of shell commands to execute
            execution_mode: 'sequential' or 'parallel'
            ignore_errors: Whether to continue on error (sequential mode only)
            working_dir: Working directory for command execution
            env_vars: Additional environment variables
            yaml_vars: YAML variables for resolution
            payload: Payload data for resolution
            payload_type: Payload type for resolution
            
        Returns:
            True if all commands succeeded, False otherwise
        """
        if execution_mode == 'sequential':
            return self._execute_sequential(commands, ignore_errors, working_dir, env_vars, yaml_vars, payload, payload_type)
        elif execution_mode == 'parallel':
            return self._execute_parallel(commands, working_dir, env_vars, yaml_vars, payload, payload_type)
        else:
            self.logger.error(f"Unknown execution mode: {execution_mode}")
            return False
    
    def _execute_sequential(
        self,
        commands: List[str],
        ignore_errors: bool,
        working_dir: str = None,
        env_vars: Dict[str, str] = None,
        yaml_vars: Dict[str, Any] = None,
        payload: Any = None,
        payload_type: str = None
    ) -> bool:
        """
        Execute commands sequentially with EXEC variable support
        
        Args:
            commands: List of commands
            ignore_errors: Whether to continue on error
            working_dir: Working directory
            env_vars: Environment variables
            yaml_vars: YAML variables
            payload: Payload data
            payload_type: Payload type
            
        Returns:
            True if all commands succeeded (or errors ignored), False otherwise
        """
        all_success = True
        
        # Initialize EXEC context
        exec_context = {
            'STDOUT': '',
            'STDERR': '',
            'OUTPUT': '',
            'RESULT': 0
        }
        
        for idx, command in enumerate(commands):
            # Create resolver with EXEC context
            resolver = VariableResolver(yaml_vars, payload, payload_type, env_vars, exec_context)
            
            try:
                resolved_command = resolver.resolve(command)
            except Exception as e:
                self.logger.error(f"Error resolving variables in command: {e}")
                if not ignore_errors:
                    return False
                continue
            
            self.logger.info(f"Executing command {idx + 1}/{len(commands)}: {resolved_command}")
            
            success, result = self._execute_single_command(resolved_command, working_dir, env_vars)
            
            # Update EXEC context for next command (regardless of success)
            if result:
                stdout = result.stdout or ''
                stderr = result.stderr or ''
                
                # Limit output size
                if len(stdout) > self.max_output_size:
                    stdout = stdout[:self.max_output_size] + '\n[truncated]'
                    self.logger.warning(f"STDOUT truncated to {self.max_output_size} bytes")
                
                if len(stderr) > self.max_output_size:
                    stderr = stderr[:self.max_output_size] + '\n[truncated]'
                    self.logger.warning(f"STDERR truncated to {self.max_output_size} bytes")
                
                exec_context['STDOUT'] = stdout.strip()
                exec_context['STDERR'] = stderr.strip()
                exec_context['OUTPUT'] = (stdout + stderr).strip()
                exec_context['RESULT'] = result.returncode
            
            if not success:
                all_success = False
                if not ignore_errors:
                    self.logger.error(f"Command failed, stopping execution: {resolved_command}")
                    return False
                else:
                    self.logger.warning(f"Command failed but continuing (ignore_errors=True): {resolved_command}")
        
        return all_success
    
    def _execute_parallel(
        self,
        commands: List[str],
        working_dir: str = None,
        env_vars: Dict[str, str] = None,
        yaml_vars: Dict[str, Any] = None,
        payload: Any = None,
        payload_type: str = None
    ) -> bool:
        """
        Execute commands in parallel
        
        Args:
            commands: List of commands
            working_dir: Working directory
            env_vars: Environment variables
            yaml_vars: YAML variables
            payload: Payload data
            payload_type: Payload type
            
        Returns:
            True if all commands succeeded, False otherwise
        """
        # In parallel mode, EXEC context is empty (no previous command)
        # Use None values so defaults will be used
        exec_context = {'STDOUT': None, 'STDERR': None, 'OUTPUT': None, 'RESULT': None}
        
        for command in commands:
            try:
                resolver = VariableResolver(yaml_vars, payload, payload_type, env_vars, exec_context)
                # Try to resolve - will fail if EXEC var without default
                resolver.resolve(command)
            except ValueError as e:
                if 'EXEC' in str(e) and 'not found' in str(e):
                    self.logger.error(f"Parallel mode requires default values for EXEC variables: {command}")
                    self.logger.error(f"Use syntax: ${{EXEC:STDOUT:-default}}")
                    return False
                raise
        
        # Execute commands in parallel
        all_success = True
        
        with ThreadPoolExecutor(max_workers=len(commands)) as executor:
            # Resolve all commands first
            resolved_commands = []
            for command in commands:
                resolver = VariableResolver(yaml_vars, payload, payload_type, env_vars, exec_context)
                resolved_commands.append(resolver.resolve(command))
            
            future_to_command = {
                executor.submit(self._execute_single_command, cmd, working_dir, env_vars): cmd
                for cmd in resolved_commands
            }
            
            for future in as_completed(future_to_command):
                command = future_to_command[future]
                try:
                    success, result = future.result()
                    if not success:
                        all_success = False
                except Exception as e:
                    self.logger.error(f"Exception executing command '{command}': {e}")
                    all_success = False
        
        return all_success
    
    def _execute_single_command(
        self,
        command: str,
        working_dir: str = None,
        env_vars: Dict[str, str] = None
    ) -> Tuple[bool, Optional[subprocess.CompletedProcess]]:
        """
        Execute a single shell command
        
        Args:
            command: Shell command to execute
            working_dir: Working directory for execution
            env_vars: Additional environment variables
            
        Returns:
            Tuple of (success, result)
        """
        try:
            # Prepare environment
            env = os.environ.copy()
            if env_vars:
                env.update(env_vars)
            
            # Log working directory if specified
            if working_dir:
                self.logger.debug(f"Working directory: {working_dir}")
            
            # Determine timeout
            timeout = None if self.command_timeout == 0 else self.command_timeout
            
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=working_dir,
                env=env
            )
            
            if result.returncode == 0:
                self.logger.info(f"Command succeeded: {command}")
                if result.stdout:
                    self.logger.debug(f"Command output: {result.stdout.strip()}")
                return True, result
            else:
                self.logger.error(f"Command failed with code {result.returncode}: {command}")
                if result.stderr:
                    self.logger.error(f"Error output: {result.stderr.strip()}")
                return False, result
                
        except subprocess.TimeoutExpired:
            self.logger.error(f"Command timed out after {self.command_timeout} seconds: {command}")
            return False, None
        except Exception as e:
            self.logger.error(f"Error executing command '{command}': {e}")
            return False, None
