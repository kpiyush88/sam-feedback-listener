#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Message Parser for SAM Feedback Listener
Parses raw message JSON and extracts minimal set of fields, storing everything else as raw JSONB data.
No field normalization - preserves original field names.
"""

import json
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ParsedMessage:
    """Parsed message with minimal normalized fields + raw JSONB-friendly data"""

    # Essential indexing fields
    id: str
    context_id: str
    timestamp: datetime
    role: str  # 'user', 'agent', 'system' (raw string, not enum)
    task_status: str  # 'working', 'completed', 'failed' (raw string, not enum)

    # Convenience fields for common queries
    message_id: Optional[str] = None
    agent_name: Optional[str] = None
    agent_id: Optional[str] = None
    message_text: Optional[str] = None
    topic: Optional[str] = None
    method: Optional[str] = None
    parent_task_id: Optional[str] = None
    feedback_id: Optional[str] = None

    # Raw message structures (for JSONB storage - no normalization)
    message_parts: Optional[List[Dict[str, Any]]] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)

    # Raw data for JSONB columns (complete unmodified data)
    raw_payload: Optional[Dict[str, Any]] = None  # Complete original payload
    raw_user_profile: Optional[Dict[str, Any]] = None  # Complete user profile with original field names
    raw_token_usage: Optional[Dict[str, Any]] = None  # Complete token usage data


class MessageParser:
    """Parses raw message JSON with minimal normalization, preserving original structure for JSONB storage"""

    def parse_message_file(self, file_path: str) -> ParsedMessage:
        """Parse a single message JSON file"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return self.parse_message(data)

    def parse_message(self, raw_message: Dict[str, Any]) -> ParsedMessage:
        """Parse raw message dictionary"""

        metadata = raw_message.get('metadata', {})
        payload = raw_message.get('payload', {})

        # Extract timestamp
        timestamp_str = metadata.get('timestamp')
        timestamp = datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now()

        # Determine message type and extract accordingly
        message_type = self._determine_message_type(payload)

        if message_type == 'params':
            return self._parse_params_message(raw_message, payload, metadata, timestamp)
        elif message_type == 'result':
            return self._parse_result_message(raw_message, payload, metadata, timestamp)
        else:
            # Fallback to minimal parsing
            return self._parse_minimal_message(raw_message, metadata, timestamp)

    def _determine_message_type(self, payload: Dict[str, Any]) -> str:
        """Determine the type of message"""
        if 'params' in payload:
            return 'params'
        elif 'result' in payload:
            return 'result'
        else:
            return 'unknown'

    def _parse_params_message(self, raw_message: Dict, payload: Dict, metadata: Dict, timestamp: datetime) -> ParsedMessage:
        """Parse a params-based message"""
        params = payload.get('params', {})
        message = params.get('message', {})

        context_id = message.get('contextId') or params.get('contextId')
        message_id = message.get('messageId')
        role = message.get('role') or params.get('role') or 'agent'

        # Extract agent_name with fallback logic
        agent_name = params.get('agent_name') or self._extract_agent_name_from_topic(metadata.get('topic'))

        parsed = ParsedMessage(
            id=payload.get('id', ''),
            context_id=context_id or '',
            timestamp=timestamp,
            role=role,
            task_status=self._extract_task_status(message, params),
            message_id=message_id,
            agent_name=agent_name,
            agent_id=metadata.get('sender_id'),
            topic=metadata.get('topic'),
            method=payload.get('method'),
            parent_task_id=message.get('parentTaskId') or message.get('parent_task_id'),
            feedback_id=metadata.get('feedback_id'),
            message_parts=message.get('parts', []),
            raw_payload=payload,
            raw_user_profile=self._extract_user_profile(metadata),
            raw_token_usage=self._extract_token_usage(message),
        )

        # Extract tool calls and results
        self._extract_tool_data(message, parsed)
        self._extract_message_text(message, parsed)

        return parsed

    def _parse_result_message(self, raw_message: Dict, payload: Dict, metadata: Dict, timestamp: datetime) -> ParsedMessage:
        """Parse a result-based message"""
        result = payload.get('result', {})

        context_id = result.get('contextId')
        status_obj = result.get('status', {})
        message = status_obj.get('message', {})

        # Extract agent_name with fallback logic
        agent_name = self._extract_agent_name(result, status_obj, metadata)

        parsed = ParsedMessage(
            id=payload.get('id', ''),
            context_id=context_id or '',
            timestamp=timestamp,
            role=message.get('role') or 'agent',
            task_status=self._extract_task_status_from_result(result),
            message_id=message.get('messageId'),
            agent_name=agent_name,
            agent_id=metadata.get('sender_id'),
            topic=metadata.get('topic'),
            parent_task_id=message.get('parentTaskId'),
            feedback_id=metadata.get('feedback_id'),
            message_parts=message.get('parts', []),
            raw_payload=payload,
            raw_user_profile=self._extract_user_profile(metadata),
            raw_token_usage=self._extract_token_usage(message),
        )

        # Extract tool calls and results
        self._extract_tool_data(message, parsed)
        self._extract_message_text(message, parsed)

        return parsed

    def _parse_minimal_message(self, raw_message: Dict, metadata: Dict, timestamp: datetime) -> ParsedMessage:
        """Parse with minimal information"""
        return ParsedMessage(
            id=raw_message.get('id', ''),
            context_id='',
            timestamp=timestamp,
            role='system',
            task_status='working',
            topic=metadata.get('topic'),
            feedback_id=metadata.get('feedback_id'),
            raw_payload=raw_message,
            raw_user_profile=self._extract_user_profile(metadata),
        )

    def _extract_task_status(self, message: Dict, params: Dict) -> str:
        """Extract task status as raw string (no enum conversion)"""
        # Check message state
        state = message.get('state') or params.get('state')
        if state:
            if state == 'working':
                return 'working'
            elif state in ('completed', 'done'):
                return 'completed'
            elif state in ('failed', 'error'):
                return 'failed'
        return 'working'

    def _extract_task_status_from_result(self, result: Dict) -> str:
        """Extract task status from result object"""
        status_obj = result.get('status', {})
        state = status_obj.get('state')
        if state:
            if state == 'working':
                return 'working'
            elif state in ('completed', 'done'):
                return 'completed'
            elif state in ('failed', 'error'):
                return 'failed'

        # Check if final
        if result.get('final'):
            return 'completed'

        return 'working'

    def _extract_agent_name(self, result: Dict, status_obj: Dict, metadata: Dict) -> Optional[str]:
        """
        Extract agent_name with multiple fallback strategies:
        1. From result.status.message.metadata.agent_name (for status-update messages)
        2. From result.metadata.agent_name
        3. From status_obj.metadata.agent_name
        4. From artifact URI in message parts (artifact://AgentName/...)
        5. From topic string parsing
        6. Return None if not found (will be populated by uploader)
        """
        # Try status.message.metadata.agent_name first (status-update messages)
        message = status_obj.get('message', {})
        agent_name = message.get('metadata', {}).get('agent_name')
        if agent_name:
            return agent_name

        # Try result metadata
        agent_name = result.get('metadata', {}).get('agent_name')
        if agent_name:
            return agent_name

        # Try status object metadata
        agent_name = status_obj.get('metadata', {}).get('agent_name')
        if agent_name:
            return agent_name

        # Try extracting from artifact URI in message parts
        agent_name = self._extract_agent_name_from_artifact(message)
        if agent_name:
            return agent_name

        # Try extracting from topic
        topic = metadata.get('topic')
        agent_name = self._extract_agent_name_from_topic(topic)
        if agent_name:
            return agent_name

        # Return None - uploader will handle population
        return None

    def _extract_agent_name_from_topic(self, topic: Optional[str]) -> Optional[str]:
        """
        Extract agent name from topic string.
        Topic format: jde-sam-test/a2a/v1/agent/status/AgentName/...
        """
        if not topic:
            return None

        parts = topic.split('/')

        # Look for /agent/status/AgentName/ pattern
        try:
            if 'agent' in parts and 'status' in parts:
                agent_idx = parts.index('agent')
                if agent_idx + 2 < len(parts) and parts[agent_idx + 1] == 'status':
                    agent_name = parts[agent_idx + 2]
                    # Filter out non-agent parts (gdk-gateway, etc.)
                    if agent_name and not agent_name.startswith('gdk-'):
                        return agent_name
        except (ValueError, IndexError):
            pass

        return None

    def _extract_agent_name_from_artifact(self, message: Dict) -> Optional[str]:
        """
        Extract agent name from artifact URI in message parts.
        Artifact URI format: artifact://AgentName/...
        """
        parts = message.get('parts', [])
        for part in parts:
            if isinstance(part, dict) and 'file' in part:
                file_info = part['file']
                if isinstance(file_info, dict) and 'uri' in file_info:
                    uri = file_info['uri']
                    # Parse artifact://AgentName/...
                    if uri.startswith('artifact://'):
                        # Extract agent name from URI (first part after artifact://)
                        agent_part = uri.replace('artifact://', '').split('/')[0]
                        if agent_part and not agent_part.startswith('gdk-'):
                            return agent_part
        return None

    def _extract_user_profile(self, metadata: Dict) -> Optional[Dict[str, Any]]:
        """Extract complete user profile with original field names (no normalization)"""
        user_properties = metadata.get('user_properties')

        # Handle null user_properties gracefully
        if user_properties is None:
            return None

        user_config = user_properties.get('a2aUserConfig', {}) if isinstance(user_properties, dict) else {}
        user_profile = user_config.get('user_profile', {})
        user_info = user_profile.get('user_info', {})

        # Return the complete raw user_profile - preserve ALL original field names
        if user_profile:
            return user_profile
        elif user_info:
            return user_info

        return None

    def _extract_token_usage(self, message: Dict) -> Optional[Dict[str, Any]]:
        """Extract token usage data as-is without normalization"""
        if not message or 'parts' not in message:
            return None

        for part in message.get('parts', []):
            if part.get('kind') == 'data':
                data = part.get('data', {})
                if data.get('type') == 'llm_response':
                    response_data = data.get('data', {})

                    # Return raw token usage - keep original field names
                    usage = data.get('usage')
                    usage_metadata = response_data.get('usage_metadata')

                    if usage:
                        return usage
                    elif usage_metadata:
                        return usage_metadata

        return None

    def _extract_tool_data(self, message: Dict, parsed: ParsedMessage) -> None:
        """Extract tool invocations and results from message parts"""
        if not message or 'parts' not in message:
            return

        for part in message.get('parts', []):
            if part.get('kind') == 'data':
                data = part.get('data', {})
                data_type = data.get('type')

                # Handle tool invocation start
                if data_type == 'tool_invocation_start':
                    parsed.tool_calls.append(data)

                # Handle tool result
                elif data_type == 'tool_result':
                    parsed.tool_results.append(data)

                # Handle llm_response with function calls
                elif data_type == 'llm_response':
                    response_data = data.get('data', {})
                    content = response_data.get('content', {})
                    parts = content.get('parts', [])

                    for p in parts:
                        if 'function_call' in p:
                            # Store function call from LLM response
                            parsed.tool_calls.append({
                                'type': 'llm_response',
                                'function_call': p['function_call']
                            })

    def _extract_message_text(self, message: Dict, parsed: ParsedMessage) -> None:
        """Extract text content from message"""
        if not message or 'parts' not in message:
            return

        text_parts = []

        for part in message.get('parts', []):
            if part.get('kind') == 'data':
                data = part.get('data', {})
                data_type = data.get('type')

                if data_type == 'llm_response':
                    response_data = data.get('data', {})
                    content = response_data.get('content', {})
                    parts = content.get('parts', [])

                    for p in parts:
                        if 'text' in p:
                            text_parts.append(p['text'])

                elif data_type == 'agent_progress_update':
                    text_parts.append(data.get('status_text', ''))

        if text_parts:
            parsed.message_text = ' '.join(text_parts).strip()


class MessageAnalyzer:
    """Helper class for analyzing messages"""

    @staticmethod
    def is_agent_message(parsed: ParsedMessage) -> bool:
        """Check if message is from agent"""
        return parsed.role == 'agent'

    @staticmethod
    def is_user_message(parsed: ParsedMessage) -> bool:
        """Check if message is from user"""
        return parsed.role == 'user'

    @staticmethod
    def has_tool_calls(parsed: ParsedMessage) -> bool:
        """Check if message has tool calls"""
        return len(parsed.tool_calls) > 0

    @staticmethod
    def has_tool_results(parsed: ParsedMessage) -> bool:
        """Check if message has tool results"""
        return len(parsed.tool_results) > 0

    @staticmethod
    def is_completed(parsed: ParsedMessage) -> bool:
        """Check if message indicates completion"""
        return parsed.task_status == 'completed'
