"""
Variable resolver module
Handles variable resolution from different sources: ENV, PAYLOAD, YAML
"""

import os
import re
import shlex
import json
from typing import Dict, Any, Optional


class VariableResolver:
    """Resolve variables from multiple sources"""
    
    def __init__(
        self,
        yaml_vars: Dict[str, Any] = None,
        payload: Any = None,
        payload_type: str = None,
        env_vars: Dict[str, str] = None,
        exec_context: Dict[str, Any] = None
    ):
        """
        Initialize variable resolver
        
        Args:
            yaml_vars: Variables from YAML configuration
            payload: Payload data (string or dict)
            payload_type: Type of payload ('string' or 'json')
            env_vars: Additional environment variables (merged with os.environ)
            exec_context: Execution context from previous command
        """
        self.yaml_vars = yaml_vars or {}
        self.payload = payload
        self.payload_type = payload_type
        self.env_vars = env_vars or {}
        self.exec_context = exec_context or {}
        self.payload_vars = self._extract_payload_vars()
    
    def _extract_payload_vars(self) -> Dict[str, Any]:
        """Extract variables from payload"""
        if self.payload is None:
            return {}
        
        if self.payload_type == 'string':
            return {'PAYLOAD': self.payload}
        elif self.payload_type == 'json':
            result = {'PAYLOAD': json.dumps(self.payload) if isinstance(self.payload, dict) else str(self.payload)}
            result.update(self._flatten_json(self.payload))
            return result
        
        return {}
    
    def _flatten_json(self, data: Any, prefix: str = '') -> Dict[str, Any]:
        """Flatten nested JSON to dot-notation keys"""
        result = {}
        
        if isinstance(data, dict):
            for key, value in data.items():
                new_key = f"{prefix}.{key}" if prefix else key
                if isinstance(value, (dict, list)):
                    result.update(self._flatten_json(value, new_key))
                else:
                    result[new_key] = value
        elif isinstance(data, list):
            for i, item in enumerate(data):
                new_key = f"{prefix}[{i}]"
                if isinstance(item, (dict, list)):
                    result.update(self._flatten_json(item, new_key))
                else:
                    result[new_key] = item
        
        return result
    
    def resolve(self, text: str, escape: bool = True) -> str:
        """
        Resolve all variables in text
        
        Syntax:
        - ${ENV:VARNAME:-default}     - Environment variable
        - ${PAYLOAD:VARNAME:-default}  - Payload variable
        - ${YAML:VARNAME:-default}     - YAML variable
        - ${EXEC:VARNAME:-default}     - Execution context (previous command result)
        - ${VARNAME:-default}          - YAML variable (default source)
        
        Args:
            text: Text containing variables
            escape: Whether to escape values for shell safety
            
        Returns:
            Text with variables resolved
        """
        # Pattern: ${SOURCE:VARNAME:-default} or ${VARNAME:-default}
        pattern = r'\$\{(?:([A-Z]+):)?([^}:]+)(?::-(.*?))?\}'
        
        def replacer(match):
            source = match.group(1) or 'YAML'  # Default to YAML
            var_name = match.group(2)
            default_value = match.group(3) if match.group(3) is not None else None
            
            # Get value from appropriate source
            value = self._get_value(source, var_name, default_value)
            
            if value is None:
                raise ValueError(
                    f"Variable '{var_name}' not found in source '{source}' and no default provided"
                )
            
            # Convert to string
            str_value = str(value)
            
            # Escape for shell safety if needed
            # EXEC variables should always be escaped for security
            if escape and source in ['PAYLOAD', 'ENV', 'EXEC']:
                str_value = shlex.quote(str_value)
            
            return str_value
        
        return re.sub(pattern, replacer, text)
    
    def _get_value(self, source: str, var_name: str, default: Optional[str]) -> Optional[Any]:
        """Get value from specified source"""
        if source == 'ENV':
            # Check additional env_vars first, then fall back to os.environ
            if var_name in self.env_vars:
                return self.env_vars[var_name]
            return os.environ.get(var_name, default)
        elif source == 'PAYLOAD':
            return self.payload_vars.get(var_name, default)
        elif source == 'YAML':
            return self.yaml_vars.get(var_name, default)
        elif source == 'EXEC':
            # For EXEC, if value is None, use default
            value = self.exec_context.get(var_name)
            return default if value is None else value
        else:
            raise ValueError(f"Unknown variable source: {source}")
    
    def resolve_dict(self, data: Dict[str, Any], escape: bool = True) -> Dict[str, Any]:
        """
        Resolve variables in a dictionary recursively
        
        Args:
            data: Dictionary containing variables
            escape: Whether to escape values
            
        Returns:
            Dictionary with variables resolved
        """
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.resolve(value, escape)
            elif isinstance(value, dict):
                result[key] = self.resolve_dict(value, escape)
            elif isinstance(value, list):
                result[key] = [
                    self.resolve(item, escape) if isinstance(item, str) else item
                    for item in value
                ]
            else:
                result[key] = value
        return result
