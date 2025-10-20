#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Message Parser for SAM Agent Messages
Parses JSON messages and extracts structured data for database storage
"""

import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
from abc import ABC, abstractmethod


class MessageRole(Enum):
    """Enumeration of message roles"""
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class TaskStatus(Enum):
    """Enumeration of task statuses"""
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class UserProfile:
    """User profile information extracted from messages"""
    # Core fields (stored in dedicated columns)
    id: Optional[str] = None
    email: Optional[str] = None
    name: Optional[str] = None
    country: Optional[str] = None
    job_grade: Optional[str] = None
    company: Optional[str] = None
    manager_id: Optional[str] = None
    location: Optional[str] = None
    language: Optional[str] = None
    authenticated: Optional[bool] = None

    # Extended fields (stored in metadata)
    job_title: Optional[str] = None
    department: Optional[str] = None
    employee_group: Optional[str] = None
    fte: Optional[str] = None
    manager_name: Optional[str] = None
    division: Optional[str] = None
    job_family: Optional[str] = None
    job_sub_family: Optional[str] = None
    cost_center: Optional[str] = None
    business_unit: Optional[str] = None
    contract_type: Optional[str] = None
    position_grade: Optional[str] = None
    salary_structure: Optional[str] = None
    security_code: Optional[str] = None
    auth_method: Optional[str] = None


@dataclass
class TokenUsage:
    """Token usage statistics"""
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    by_model: Dict[str, Dict[str, int]] = field(default_factory=dict)


@dataclass
class ParsedMessage:
    """Structured representation of a parsed message"""
    # Metadata from file
    topic: str
    agent_id: str  # ID of the agent that sent this message
    timestamp: datetime
    message_number: int

    # From payload
    id: str  # task or message ID
    jsonrpc: str
    method: Optional[str] = None

    # Message details
    context_id: Optional[str] = None
    message_id: Optional[str] = None
    role: Optional[MessageRole] = None
    agent_name: Optional[str] = None
    message_parts: List[Dict] = field(default_factory=list)
    message_text: Optional[str] = None

    # Task details
    parent_task_id: Optional[str] = None
    task_status: Optional[TaskStatus] = None
    function_call_id: Optional[str] = None

    # Token usage
    token_usage: Optional[TokenUsage] = None

    # Artifacts
    artifacts_produced: List[Dict] = field(default_factory=list)

    # Tool/function calls
    tool_calls: List[Dict] = field(default_factory=list)

    # User profile
    user_profile: Optional[UserProfile] = None

    # Original payload for reference
    raw_payload: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, handling enums and datetime"""
        result = asdict(self)
        result['timestamp'] = self.timestamp.isoformat() if self.timestamp else None
        result['role'] = self.role.value if self.role else None
        result['task_status'] = self.task_status.value if self.task_status else None
        return result


class PayloadParser(ABC):
    """Abstract base class for payload parsers"""

    @abstractmethod
    def can_parse(self, payload: Dict) -> bool:
        """Check if this parser can handle the given payload"""
        pass

    @abstractmethod
    def parse(self, payload: Dict, parsed: ParsedMessage) -> None:
        """Parse the payload and populate the ParsedMessage"""
        pass


class ParamsParser(PayloadParser):
    """Parser for message params"""

    def can_parse(self, payload: Dict) -> bool:
        return 'params' in payload

    def parse(self, payload: Dict, parsed: ParsedMessage) -> None:
        params = payload['params']
        message = params.get('message', {})

        parsed.context_id = message.get('contextId')
        parsed.message_id = message.get('messageId')

        # Parse role
        role_str = message.get('role')
        if role_str:
            try:
                parsed.role = MessageRole(role_str)
            except ValueError:
                parsed.role = None

        # Parse message parts
        parts = message.get('parts', [])
        parsed.message_parts = parts

        # Extract text content
        text_parts = [p.get('text', '') for p in parts if p.get('kind') == 'text']
        parsed.message_text = ' '.join(text_parts) if text_parts else None

        # Parse metadata
        msg_metadata = message.get('metadata', {})
        parsed.agent_name = msg_metadata.get('agent_name')
        parsed.parent_task_id = msg_metadata.get('parentTaskId')
        parsed.function_call_id = msg_metadata.get('function_call_id')

        # Extract tool calls from parts
        parsed.tool_calls = [
            p.get('data', {}) for p in parts
            if p.get('kind') == 'data' and p.get('data', {}).get('type') == 'llm_invocation'
        ]

        # Parse user profile if present (in system instructions)
        UserProfileExtractor.extract(parts, parsed)


class ResultParser(PayloadParser):
    """Parser for message results"""

    def can_parse(self, payload: Dict) -> bool:
        return 'result' in payload

    def parse(self, payload: Dict, parsed: ParsedMessage) -> None:
        result = payload['result']
        parsed.context_id = result.get('contextId')

        # Delegate to specialized result parsers
        if result.get('kind') == 'status-update':
            StatusUpdateParser.parse(result, parsed)
        elif result.get('kind') == 'task':
            TaskResultParser.parse(result, parsed)


class StatusUpdateParser:
    """Parser for status update results"""

    @staticmethod
    def parse(result: Dict, parsed: ParsedMessage) -> None:
        status_data = result.get('status', {})
        status_str = status_data.get('state')
        if status_str:
            try:
                parsed.task_status = TaskStatus(status_str)
            except ValueError:
                parsed.task_status = None

        # Parse status message
        status_message = status_data.get('message', {})
        if status_message:
            parsed.message_id = status_message.get('messageId')
            parsed.agent_name = result.get('metadata', {}).get('agent_name')
            parsed.role = MessageRole.AGENT

            parts = status_message.get('parts', [])
            parsed.message_parts = parts

            # Extract tool calls from status update
            parsed.tool_calls = [
                p.get('data', {}) for p in parts
                if p.get('kind') == 'data' and p.get('data', {}).get('type') == 'llm_invocation'
            ]


class TaskResultParser:
    """Parser for task results"""

    @staticmethod
    def parse(result: Dict, parsed: ParsedMessage) -> None:
        parsed.task_status = TaskStatus.COMPLETED
        metadata = result.get('metadata', {})
        parsed.agent_name = metadata.get('agent_name')
        parsed.artifacts_produced = metadata.get('produced_artifacts', [])

        # Parse token usage
        token_data = metadata.get('token_usage', {})
        if token_data:
            parsed.token_usage = TokenUsage(
                total_tokens=token_data.get('total_tokens', 0),
                input_tokens=token_data.get('total_input_tokens', 0),
                output_tokens=token_data.get('total_output_tokens', 0),
                cached_tokens=token_data.get('total_cached_input_tokens', 0),
                by_model=token_data.get('by_model', {})
            )

        # Parse final message
        status = result.get('status', {})
        if status:
            message = status.get('message', {})
            if message:
                parsed.message_id = message.get('messageId')
                parsed.role = MessageRole.AGENT

                parts = message.get('parts', [])
                parsed.message_parts = parts

                text_parts = [p.get('text', '') for p in parts if p.get('kind') == 'text']
                parsed.message_text = ' '.join(text_parts) if text_parts else None


class UserProfileExtractor:
    """Extracts user profile information from message parts and metadata"""

    @staticmethod
    def extract_from_metadata(metadata: Dict) -> Optional[UserProfile]:
        """Extract complete user profile from top-level metadata.user_properties"""
        try:
            user_props = metadata.get('user_properties', {})
            a2a_config = user_props.get('a2aUserConfig', {})
            user_profile_data = a2a_config.get('user_profile', {})

            if user_profile_data:
                user_info = user_profile_data.get('user_info', user_profile_data)
                return UserProfile(
                    # Core fields
                    id=user_info.get('id') or user_profile_data.get('id'),
                    email=user_info.get('email') or user_profile_data.get('workEmail'),
                    name=user_info.get('name') or user_profile_data.get('displayName'),
                    country=user_info.get('country') or user_profile_data.get('country'),
                    job_grade=user_info.get('jobGrade') or user_info.get('positionGrade') or user_profile_data.get('jobGrade'),
                    company=user_info.get('company') or user_profile_data.get('company'),
                    manager_id=user_info.get('manager') or user_profile_data.get('manager'),
                    location=user_info.get('location') or user_profile_data.get('location'),
                    language=user_info.get('nativePreferredLanguage') or user_profile_data.get('nativePreferredLanguage'),
                    authenticated=user_info.get('authenticated'),

                    # Extended fields
                    job_title=user_info.get('jobTitle') or user_profile_data.get('jobTitle'),
                    department=user_info.get('department') or user_profile_data.get('department'),
                    employee_group=user_info.get('employeeGroup') or user_profile_data.get('employeeGroup'),
                    fte=user_info.get('fte') or user_profile_data.get('fte'),
                    manager_name=user_info.get('managerName') or user_profile_data.get('managerName'),
                    division=user_info.get('division') or user_profile_data.get('division'),
                    job_family=user_info.get('jobFamily') or user_profile_data.get('jobFamily'),
                    job_sub_family=user_info.get('jobSubFamily') or user_profile_data.get('jobSubFamily'),
                    cost_center=user_info.get('costCenter') or user_profile_data.get('costCenter'),
                    business_unit=user_info.get('businessUnit') or user_profile_data.get('businessUnit'),
                    contract_type=user_info.get('contractType') or user_profile_data.get('contractType'),
                    position_grade=user_info.get('positionGrade') or user_profile_data.get('positionGrade'),
                    salary_structure=user_info.get('salaryStructure') or user_profile_data.get('salaryStructure'),
                    security_code=user_info.get('securityCode') or user_profile_data.get('securityCode'),
                    auth_method=user_info.get('auth_method') or user_profile_data.get('auth_method')
                )
        except Exception:
            pass
        return None

    @staticmethod
    def extract(parts: List[Dict], parsed: ParsedMessage) -> None:
        """Extract user profile from message parts (fallback method)"""
        for part in parts:
            if part.get('kind') == 'data':
                data = part.get('data', {})
                if data.get('type') == 'llm_invocation':
                    request = data.get('request', {})
                    config = request.get('config', {})
                    system_instruction = config.get('system_instruction', '')

                    # Look for user profile JSON in system instruction
                    if 'user_info' in system_instruction or 'User Profile' in system_instruction:
                        profile_data = UserProfileExtractor._extract_json(system_instruction)
                        if profile_data:
                            parsed.user_profile = UserProfileExtractor._create_profile(profile_data)

    @staticmethod
    def _extract_json(system_instruction: str) -> Optional[Dict]:
        """Extract JSON object from system instruction"""
        try:
            start_idx = system_instruction.find('"user_info"')
            if start_idx == -1:
                start_idx = system_instruction.find('"id"')

            if start_idx != -1:
                # Find the surrounding JSON object
                brace_count = 0
                json_start = system_instruction.rfind('{', 0, start_idx)
                json_end = -1

                for i in range(json_start, len(system_instruction)):
                    if system_instruction[i] == '{':
                        brace_count += 1
                    elif system_instruction[i] == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            json_end = i + 1
                            break

                if json_end != -1:
                    profile_json = system_instruction[json_start:json_end]
                    return json.loads(profile_json)
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    @staticmethod
    def _create_profile(profile_data: Dict) -> UserProfile:
        """Create UserProfile from extracted data"""
        user_info = profile_data.get('user_info', profile_data)
        return UserProfile(
            # Core fields
            id=user_info.get('id'),
            email=user_info.get('email') or user_info.get('workEmail'),
            name=user_info.get('name') or user_info.get('displayName'),
            country=user_info.get('country'),
            job_grade=user_info.get('jobGrade') or user_info.get('positionGrade'),
            company=user_info.get('company'),
            manager_id=user_info.get('manager'),
            location=user_info.get('location'),
            language=user_info.get('nativePreferredLanguage'),
            authenticated=user_info.get('authenticated'),

            # Extended fields
            job_title=user_info.get('jobTitle'),
            department=user_info.get('department'),
            employee_group=user_info.get('employeeGroup'),
            fte=user_info.get('fte'),
            manager_name=user_info.get('managerName'),
            division=user_info.get('division'),
            job_family=user_info.get('jobFamily'),
            job_sub_family=user_info.get('jobSubFamily'),
            cost_center=user_info.get('costCenter'),
            business_unit=user_info.get('businessUnit'),
            contract_type=user_info.get('contractType'),
            position_grade=user_info.get('positionGrade'),
            salary_structure=user_info.get('salaryStructure'),
            security_code=user_info.get('securityCode'),
            auth_method=user_info.get('auth_method')
        )


class MessageAnalyzer:
    """Analyzes parsed messages to extract higher-level information"""

    @staticmethod
    def identify_conversation_id(parsed: ParsedMessage) -> str:
        """Get the conversation/context ID"""
        return parsed.context_id or 'unknown'

    @staticmethod
    def identify_main_task_id(parsed: ParsedMessage) -> str:
        """Get the main task ID (parent if exists, otherwise current)"""
        return parsed.parent_task_id or parsed.id

    @staticmethod
    def is_subtask(parsed: ParsedMessage) -> bool:
        """Check if this is a subtask"""
        return parsed.parent_task_id is not None

    @staticmethod
    def extract_query(parsed: ParsedMessage) -> Optional[str]:
        """Extract the user query from the message"""
        if parsed.role == MessageRole.USER and parsed.message_text:
            # Filter out system messages
            text = parsed.message_text
            if "Request received by gateway" not in text:
                return text
        return None

    @staticmethod
    def extract_response(parsed: ParsedMessage) -> Optional[str]:
        """Extract the agent response from the message"""
        if parsed.role == MessageRole.AGENT and parsed.message_text:
            return parsed.message_text
        return None


class MessageParser:
    """Main parser class for SAM agent messages"""

    def __init__(self):
        """Initialize the parser with payload parsers"""
        self.payload_parsers: List[PayloadParser] = [
            ParamsParser(),
            ResultParser()
        ]

    def parse_message_file(self, file_path: str) -> ParsedMessage:
        """Parse a message JSON file"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return self.parse_message(data)

    def parse_message(self, data: Dict) -> ParsedMessage:
        """Parse a message dictionary"""
        metadata = data.get('metadata', {})
        payload = data.get('payload', {})

        # Parse timestamp
        timestamp_str = metadata.get('timestamp')
        timestamp = datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now()

        # Initialize parsed message with metadata
        parsed = ParsedMessage(
            topic=metadata.get('topic', ''),
            agent_id=metadata.get('feedback_id', ''),  # Still read from 'feedback_id' in existing files
            timestamp=timestamp,
            message_number=metadata.get('message_number', 0),
            id=payload.get('id', ''),
            jsonrpc=payload.get('jsonrpc', ''),
            method=payload.get('method'),
            raw_payload=payload
        )

        # Extract user profile from metadata FIRST (priority source)
        parsed.user_profile = UserProfileExtractor.extract_from_metadata(metadata)

        # Find appropriate parser and parse payload
        for parser in self.payload_parsers:
            if parser.can_parse(payload):
                parser.parse(payload, parsed)
                break

        # If still no user profile, try extracting from parts (fallback)
        if not parsed.user_profile and parsed.message_parts:
            UserProfileExtractor.extract(parsed.message_parts, parsed)

        return parsed


def main():
    """Test the parser"""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python message_parser.py <json_file>")
        sys.exit(1)

    file_path = sys.argv[1]
    parser = MessageParser()
    parsed = parser.parse_message_file(file_path)

    print(json.dumps(parsed.to_dict(), indent=2, default=str))


if __name__ == "__main__":
    main()
