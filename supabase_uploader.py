#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Supabase Uploader for SAM Agent Messages
Uploads parsed messages to Supabase database
"""

import os
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path
from abc import ABC, abstractmethod
from supabase import create_client, Client
from message_parser import MessageParser, ParsedMessage, MessageRole, TaskStatus


class DatabaseCache:
    """Manages caching of database IDs to avoid duplicate lookups"""

    def __init__(self):
        self._conversation_cache: Dict[str, str] = {}  # context_id -> context_id
        self._task_cache: Dict[str, str] = {}  # task_id -> task_id

    def get_conversation(self, context_id: str) -> Optional[str]:
        """Get conversation ID from cache"""
        return self._conversation_cache.get(context_id)

    def cache_conversation(self, context_id: str) -> None:
        """Add conversation ID to cache"""
        self._conversation_cache[context_id] = context_id

    def get_task(self, task_id: str) -> Optional[str]:
        """Get task ID from cache"""
        return self._task_cache.get(task_id)

    def cache_task(self, task_id: str) -> None:
        """Add task ID to cache"""
        self._task_cache[task_id] = task_id

    def clear(self) -> None:
        """Clear all cached data"""
        self._conversation_cache.clear()
        self._task_cache.clear()


class MessageTypeDetector:
    """Determines semantic message type"""

    @staticmethod
    def detect(parsed: ParsedMessage) -> str:
        """Determine semantic message type from parsed message"""
        if parsed.role == MessageRole.USER:
            return 'user_query'

        # Agent messages - determine based on context
        if parsed.task_status == TaskStatus.WORKING:
            # Check if it has tool invocation
            if parsed.tool_calls:
                return 'tool_invocation'
            return 'status_update'

        if parsed.task_status == TaskStatus.COMPLETED:
            return 'final_response'

        # Default based on method if available
        if parsed.method == 'message/send':
            return 'agent_request'
        elif parsed.method == 'message/stream':
            return 'streamed_message'

        return 'agent_message'


class DataMapper:
    """Maps ParsedMessage data to database schema"""

    @staticmethod
    def extract_message_kind(parsed: ParsedMessage) -> Optional[str]:
        """Extract message kind from payload"""
        # Check in params.message.kind
        if hasattr(parsed, 'raw_payload'):
            params = parsed.raw_payload.get('params', {})
            message = params.get('message', {})
            if 'kind' in message:
                return message.get('kind')

            # Check in result.kind
            result = parsed.raw_payload.get('result', {})
            if 'kind' in result:
                return result.get('kind')

        return None

    @staticmethod
    def extract_is_partial(parsed: ParsedMessage) -> bool:
        """Check if message is partial"""
        if not parsed.message_parts:
            return False

        for part in parsed.message_parts:
            if part.get('kind') == 'data':
                data = part.get('data', {})
                if data.get('type') == 'llm_response':
                    response_data = data.get('data', {})
                    if response_data.get('partial'):
                        return True

        return False

    @staticmethod
    def extract_is_final(parsed: ParsedMessage) -> bool:
        """Check if this is a final result"""
        if hasattr(parsed, 'raw_payload'):
            result = parsed.raw_payload.get('result', {})
            if 'final' in result:
                return result.get('final', False)

        return False

    @staticmethod
    def extract_result_id(parsed: ParsedMessage) -> Optional[str]:
        """Extract result.id field"""
        if hasattr(parsed, 'raw_payload'):
            result = parsed.raw_payload.get('result', {})
            if 'id' in result:
                return result.get('id')

        return None

    @staticmethod
    def extract_per_message_token_usage(parsed: ParsedMessage) -> Optional[Dict]:
        """Extract token usage from individual message parts (llm_response)"""
        if not parsed.message_parts:
            return None

        for part in parsed.message_parts:
            if part.get('kind') == 'data':
                data = part.get('data', {})

                if data.get('type') == 'llm_response':
                    response_data = data.get('data', {})
                    usage_metadata = response_data.get('usage_metadata', {})
                    usage = data.get('usage', {})

                    if usage_metadata or usage:
                        return {
                            'model': usage.get('model'),
                            'prompt_tokens': usage_metadata.get('prompt_token_count', 0),
                            'candidates_tokens': usage_metadata.get('candidates_token_count', 0),
                            'total_tokens': usage_metadata.get('total_token_count', 0),
                            'input_tokens': usage.get('input_tokens', 0),
                            'output_tokens': usage.get('output_tokens', 0)
                        }

        return None

    @staticmethod
    def extract_correlation_id(parsed: ParsedMessage) -> Optional[str]:
        """Extract correlation ID from topic"""
        if parsed.topic:
            parts = parsed.topic.split('/')
            if len(parts) > 0:
                # Last part is often the task/correlation ID
                last_part = parts[-1]
                if last_part.startswith('gdk-task-') or last_part.startswith('a2a_subtask_'):
                    return last_part

        return None

    @staticmethod
    def extract_content_summary(parsed: ParsedMessage) -> Optional[str]:
        """Extract or generate content summary from message"""
        # If we already have message_text, use it
        if parsed.message_text:
            return parsed.message_text

        # Otherwise, try to generate a summary from parts
        if parsed.tool_calls:
            # Extract tool names for summary
            tool_names = []
            for tool_call in parsed.tool_calls:
                if tool_call.get('type') == 'llm_invocation':
                    request = tool_call.get('request', {})
                    tools = request.get('config', {}).get('tools', [])
                    for tool_group in tools:
                        for func in tool_group.get('function_declarations', []):
                            tool_names.append(func.get('name'))

            if tool_names:
                return f"Calling tools: {', '.join(tool_names[:5])}" + ("..." if len(tool_names) > 5 else "")

        # Check for status updates
        if parsed.task_status == TaskStatus.WORKING:
            return "Task in progress"
        elif parsed.task_status == TaskStatus.COMPLETED:
            return "Task completed"

        return None

    @staticmethod
    def to_conversation(parsed: ParsedMessage) -> Dict[str, Any]:
        """Map ParsedMessage to conversation data"""
        conv_data = {
            'context_id': parsed.context_id,
            'started_at': parsed.timestamp.isoformat(),
            'metadata': {}
        }

        # Add user profile if available
        if parsed.user_profile:
            # Core user fields in dedicated columns
            conv_data['user_id'] = parsed.user_profile.id
            conv_data['user_email'] = parsed.user_profile.email
            conv_data['user_name'] = parsed.user_profile.name
            conv_data['user_country'] = parsed.user_profile.country
            conv_data['user_job_grade'] = parsed.user_profile.job_grade
            conv_data['user_company'] = parsed.user_profile.company
            conv_data['user_manager_id'] = parsed.user_profile.manager_id
            conv_data['user_location'] = parsed.user_profile.location
            conv_data['user_language'] = parsed.user_profile.language
            conv_data['user_authenticated'] = parsed.user_profile.authenticated

            # Extended user fields in metadata
            conv_data['metadata'] = {
                'user_profile': {
                    'job_title': parsed.user_profile.job_title,
                    'department': parsed.user_profile.department,
                    'employee_group': parsed.user_profile.employee_group,
                    'fte': parsed.user_profile.fte,
                    'manager_name': parsed.user_profile.manager_name,
                    'division': parsed.user_profile.division,
                    'job_family': parsed.user_profile.job_family,
                    'job_sub_family': parsed.user_profile.job_sub_family,
                    'cost_center': parsed.user_profile.cost_center,
                    'business_unit': parsed.user_profile.business_unit,
                    'contract_type': parsed.user_profile.contract_type,
                    'position_grade': parsed.user_profile.position_grade,
                    'salary_structure': parsed.user_profile.salary_structure,
                    'security_code': parsed.user_profile.security_code,
                    'auth_method': parsed.user_profile.auth_method
                }
            }

        return conv_data

    @staticmethod
    def to_task(parsed: ParsedMessage, context_id: str) -> Dict[str, Any]:
        """Map ParsedMessage to task data"""
        task_type = 'subtask' if parsed.parent_task_id else 'main'

        task_data = {
            'context_id': context_id,
            'task_id': parsed.id,
            'parent_task_id': parsed.parent_task_id,
            'agent_name': parsed.agent_name,
            'task_type': task_type,
            'status': parsed.task_status.value if parsed.task_status else 'working',
            'started_at': parsed.timestamp.isoformat(),
            'metadata': {
                'topic': parsed.topic,
                'method': parsed.method,
                'is_final': DataMapper.extract_is_final(parsed),
                'result_id': DataMapper.extract_result_id(parsed)
            }
        }

        return task_data

    @staticmethod
    def to_message(parsed: ParsedMessage, context_id: str, task_id: str) -> Dict[str, Any]:
        """Map ParsedMessage to message data"""
        # Extract per-message token usage
        token_usage = DataMapper.extract_per_message_token_usage(parsed)

        message_data = {
            'context_id': context_id,
            'task_id': task_id,
            'message_id': parsed.message_id,
            'role': parsed.role.value if parsed.role else 'system',
            'message_type': MessageTypeDetector.detect(parsed),  # Semantic message type
            'agent_name': parsed.agent_name,
            'content': DataMapper.extract_content_summary(parsed),  # Extract or generate content
            'parts': parsed.message_parts,  # FULL parts data, no sanitization
            'timestamp': parsed.timestamp.isoformat(),
            'topic': parsed.topic,
            'correlation_id': DataMapper.extract_correlation_id(parsed),  # Extract from topic
            'metadata': {
                'agent_id': parsed.agent_id,
                'message_number': parsed.message_number,
                'function_call_id': parsed.function_call_id,
                'method': parsed.method,  # Keep original JSON-RPC method for reference
                'message_kind': DataMapper.extract_message_kind(parsed),
                'is_partial': DataMapper.extract_is_partial(parsed),
                'token_usage': token_usage  # Per-message token usage if available
            }
        }

        # Add per-message token fields if available
        if token_usage:
            message_data['metadata']['model_used'] = token_usage.get('model')
            message_data['metadata']['input_tokens'] = token_usage.get('input_tokens')
            message_data['metadata']['output_tokens'] = token_usage.get('output_tokens')
            message_data['metadata']['total_tokens'] = token_usage.get('total_tokens')

        return message_data

    @staticmethod
    def to_task_update(parsed: ParsedMessage) -> Dict[str, Any]:
        """Map ParsedMessage to task update data"""
        update_data = {}

        if parsed.task_status:
            update_data['status'] = parsed.task_status.value
            if parsed.task_status == TaskStatus.COMPLETED:
                update_data['completed_at'] = parsed.timestamp.isoformat()

        if parsed.token_usage:
            update_data['total_tokens'] = parsed.token_usage.total_tokens
            update_data['input_tokens'] = parsed.token_usage.input_tokens
            update_data['output_tokens'] = parsed.token_usage.output_tokens
            update_data['cached_tokens'] = parsed.token_usage.cached_tokens

            # Extract model used
            if parsed.token_usage.by_model:
                model_names = list(parsed.token_usage.by_model.keys())
                if model_names:
                    update_data['model_used'] = model_names[0]

        if parsed.artifacts_produced:
            update_data['artifacts_produced'] = parsed.artifacts_produced

        return update_data


class ConversationManager:
    """Manages conversation records in Supabase"""

    def __init__(self, client: Client, cache: DatabaseCache):
        self.client = client
        self.cache = cache

    def ensure_exists(self, parsed: ParsedMessage) -> str:
        """Ensure conversation exists in database, return context_id"""
        context_id = parsed.context_id

        # Check cache
        if self.cache.get_conversation(context_id):
            return context_id

        # Check if exists
        response = self.client.table('conversations').select('context_id').eq('context_id', context_id).execute()

        if not response.data:
            # Create new conversation
            conv_data = DataMapper.to_conversation(parsed)
            self.client.table('conversations').insert(conv_data).execute()

        # Cache it
        self.cache.cache_conversation(context_id)
        return context_id

    def update_stats(self, parsed: ParsedMessage, context_id: str) -> None:
        """Update conversation statistics"""
        # Get current stats
        response = self.client.table('conversations').select('*').eq('context_id', context_id).execute()

        if not response.data:
            return

        conv = response.data[0]
        update_data = {}

        # Update message count
        update_data['total_messages'] = conv.get('total_messages', 0) + 1

        # Update token counts
        if parsed.token_usage:
            update_data['total_tokens'] = conv.get('total_tokens', 0) + parsed.token_usage.total_tokens
            update_data['total_input_tokens'] = conv.get('total_input_tokens', 0) + parsed.token_usage.input_tokens
            update_data['total_output_tokens'] = conv.get('total_output_tokens', 0) + parsed.token_usage.output_tokens
            update_data['total_cached_tokens'] = conv.get('total_cached_tokens', 0) + parsed.token_usage.cached_tokens

        # Update end time
        update_data['ended_at'] = parsed.timestamp.isoformat()

        self.client.table('conversations').update(update_data).eq('context_id', context_id).execute()


class TaskManager:
    """Manages task records in Supabase"""

    def __init__(self, client: Client, cache: DatabaseCache):
        self.client = client
        self.cache = cache

    def ensure_exists(self, parsed: ParsedMessage, context_id: str) -> str:
        """Ensure task exists in database, return task_id"""
        task_id = parsed.id

        # Check cache
        if self.cache.get_task(task_id):
            return task_id

        # Check if exists
        response = self.client.table('tasks').select('task_id').eq('task_id', task_id).execute()

        if not response.data:
            # Create new task
            task_data = DataMapper.to_task(parsed, context_id)
            self.client.table('tasks').insert(task_data).execute()

        # Cache it
        self.cache.cache_task(task_id)
        return task_id

    def update(self, parsed: ParsedMessage, task_id: str) -> None:
        """Update task with token usage and status"""
        update_data = DataMapper.to_task_update(parsed)

        if update_data:
            self.client.table('tasks').update(update_data).eq('task_id', task_id).execute()


class MessageManager:
    """Manages message records in Supabase"""

    def __init__(self, client: Client):
        self.client = client

    def insert(self, parsed: ParsedMessage, context_id: str, task_id: str) -> str:
        """Insert message into database, return message_id"""
        message_data = DataMapper.to_message(parsed, context_id, task_id)
        self.client.table('messages').insert(message_data).execute()
        return parsed.message_id


class ToolCallManager:
    """Manages tool call records in Supabase"""

    def __init__(self, client: Client):
        self.client = client

    def insert_batch(self, parsed: ParsedMessage, message_id: str, task_id: str) -> List[str]:
        """Insert tool calls into database, return list of UUIDs"""
        tool_call_ids = []

        for tool_call in parsed.tool_calls:
            if tool_call.get('type') == 'llm_invocation':
                request = tool_call.get('request', {})
                tools = request.get('config', {}).get('tools', [])

                for tool_group in tools:
                    function_declarations = tool_group.get('function_declarations', [])

                    for func in function_declarations:
                        tool_data = {
                            'message_id': message_id,
                            'task_id': task_id,
                            'tool_name': func.get('name'),
                            'parameters': func.get('parameters'),
                            'status': 'called',
                            'timestamp': parsed.timestamp.isoformat()
                        }

                        response = self.client.table('tool_calls').insert(tool_data).execute()
                        tool_call_ids.append(response.data[0]['id'])

        return tool_call_ids


class SupabaseUploader:
    """Main uploader class that coordinates all managers"""

    def __init__(self, supabase_url: str = None, supabase_key: str = None):
        """Initialize Supabase client and managers"""
        self.url = supabase_url or os.getenv("SUPABASE_URL")
        self.key = supabase_key or os.getenv("SUPABASE_KEY")

        if not self.url or not self.key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be provided")

        self.client: Client = create_client(self.url, self.key)

        # Initialize cache and managers
        self.cache = DatabaseCache()
        self.conversation_manager = ConversationManager(self.client, self.cache)
        self.task_manager = TaskManager(self.client, self.cache)
        self.message_manager = MessageManager(self.client)
        self.tool_call_manager = ToolCallManager(self.client)

    def upload_message(self, parsed: ParsedMessage) -> Dict[str, str]:
        """
        Upload a parsed message to Supabase
        Returns dict with created record IDs
        """
        result = {}

        try:
            # 1. Ensure conversation exists
            if parsed.context_id:
                context_id = self.conversation_manager.ensure_exists(parsed)
                result['conversation_id'] = context_id

                # 2. Ensure task exists
                if parsed.id:
                    task_id = self.task_manager.ensure_exists(parsed, context_id)
                    result['task_id'] = task_id

                    # 3. Insert message
                    if parsed.message_id:
                        message_id = self.message_manager.insert(parsed, context_id, task_id)
                        result['message_id'] = message_id

                        # 4. Insert tool calls if any
                        if parsed.tool_calls:
                            tool_call_ids = self.tool_call_manager.insert_batch(parsed, message_id, task_id)
                            result['tool_call_ids'] = tool_call_ids

                    # 5. Update task with token usage and completion
                    self.task_manager.update(parsed, task_id)

                # 6. Update conversation stats
                self.conversation_manager.update_stats(parsed, context_id)

        except Exception as e:
            print(f"Error uploading message: {e}")
            result['error'] = str(e)

        return result

    def batch_upload_from_directory(self, directory: str, pattern: str = "*.json") -> Dict[str, Any]:
        """
        Upload all JSON files from a directory
        Returns statistics about the upload
        """
        stats = {
            'total_files': 0,
            'successful': 0,
            'failed': 0,
            'errors': []
        }

        directory_path = Path(directory)
        parser = MessageParser()

        for json_file in directory_path.glob(pattern):
            stats['total_files'] += 1

            try:
                parsed = parser.parse_message_file(str(json_file))
                result = self.upload_message(parsed)

                if 'error' in result:
                    stats['failed'] += 1
                    stats['errors'].append({
                        'file': str(json_file),
                        'error': result['error']
                    })
                else:
                    stats['successful'] += 1

            except Exception as e:
                stats['failed'] += 1
                stats['errors'].append({
                    'file': str(json_file),
                    'error': str(e)
                })

        return stats


def main():
    """Test the uploader"""
    import sys
    from dotenv import load_dotenv

    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: python supabase_uploader.py <json_file_or_directory>")
        sys.exit(1)

    path = sys.argv[1]
    uploader = SupabaseUploader()
    parser = MessageParser()

    path_obj = Path(path)

    if path_obj.is_file():
        # Upload single file
        parsed = parser.parse_message_file(path)
        result = uploader.upload_message(parsed)
        print(json.dumps(result, indent=2, default=str))

    elif path_obj.is_dir():
        # Upload directory
        stats = uploader.batch_upload_from_directory(path)
        print(json.dumps(stats, indent=2, default=str))

    else:
        print(f"Error: {path} is not a valid file or directory")
        sys.exit(1)


if __name__ == "__main__":
    main()
