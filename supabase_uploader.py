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
try:
    from supabase import create_client, Client  # type: ignore
except ImportError:  # Allow tests to run without supabase installed
    class Client:  # type: ignore
        pass
    def create_client(url: str, key: str):  # type: ignore
        raise ImportError("supabase package not installed. Install with 'pip install supabase'.")
from message_parser import MessageParser, ParsedMessage, MessageRole, TaskStatus


class DatabaseCache:
    """Simple existence cache for conversation IDs (with consolidated schema, tasks are part of messages)"""

    def __init__(self):
        self._conversations: set[str] = set()

    def has_conversation(self, context_id: str) -> bool:
        return context_id in self._conversations

    def cache_conversation(self, context_id: str) -> None:
        self._conversations.add(context_id)

    def clear(self) -> None:
        self._conversations.clear()


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
        """
        Extract token usage from ParsedMessage.
        Priority 1: Use aggregated token_usage from ParsedMessage (best source)
        Priority 2: Extract from message parts (fallback)
        """
        # Priority 1: Use aggregated token_usage from ParsedMessage
        if parsed.token_usage and (parsed.token_usage.input_tokens > 0 or
                                     parsed.token_usage.output_tokens > 0):
            # Get the first model name if available
            model = None
            if parsed.token_usage.by_model:
                model = list(parsed.token_usage.by_model.keys())[0]

            return {
                'model': model,
                'input_tokens': parsed.token_usage.input_tokens,
                'output_tokens': parsed.token_usage.output_tokens,
                'total_tokens': parsed.token_usage.total_tokens,
            }

        # Priority 2: Extract from message parts (fallback for older parsing)
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
                        # Prefer usage over usage_metadata
                        input_tokens = usage.get('input_tokens') or usage_metadata.get('prompt_token_count', 0)
                        output_tokens = usage.get('output_tokens') or usage_metadata.get('candidates_token_count', 0)
                        total_tokens = input_tokens + output_tokens if (input_tokens or output_tokens) else usage_metadata.get('total_token_count', 0)

                        return {
                            'model': usage.get('model'),
                            'input_tokens': input_tokens,
                            'output_tokens': output_tokens,
                            'total_tokens': total_tokens,
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
        data: Dict[str, Any] = {
            'context_id': parsed.context_id,
            'started_at': parsed.timestamp.isoformat(),
            'metadata': {}
        }
        if parsed.user_profile:
            data['user_email'] = parsed.user_profile.email
            data['user_name'] = parsed.user_profile.name
            data['user_country'] = parsed.user_profile.country
            data['user_id'] = parsed.user_profile.id
            data['user_company'] = parsed.user_profile.company
            data['user_location'] = parsed.user_profile.location
            data['user_language'] = parsed.user_profile.language
            data['user_authenticated'] = parsed.user_profile.authenticated
            data['metadata']['user_profile'] = {
                'job_title': parsed.user_profile.job_title,
                'job_grade': parsed.user_profile.job_grade,
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
        return data

    @staticmethod
    def to_task_data(parsed: ParsedMessage) -> Dict[str, Any]:
        """Extract task data for storage in messages table JSONB column"""
        task_type = 'subtask' if parsed.parent_task_id else 'main'
        is_final = DataMapper.extract_is_final(parsed)
        result_id = DataMapper.extract_result_id(parsed)
        return {
            'task_id': parsed.id,
            'parent_task_id': parsed.parent_task_id,
            'agent_name': parsed.agent_name,
            'task_type': task_type,
            'status': parsed.task_status.value if parsed.task_status else 'working',
            'started_at': parsed.timestamp.isoformat(),
            'is_final': bool(is_final),
            'result_id': result_id,
            'metadata': {
                'topic': parsed.topic,
                'method': parsed.method
            }
        }

    @staticmethod
    def to_message(parsed: ParsedMessage, context_id: str, task_id: Optional[str] = None, interaction_id: Optional[str] = None) -> Dict[str, Any]:
        """Convert ParsedMessage to new consolidated schema with JSONB columns"""
        token_usage = DataMapper.extract_per_message_token_usage(parsed)
        message_kind = DataMapper.extract_message_kind(parsed)
        is_partial = DataMapper.extract_is_partial(parsed)
        input_tokens = token_usage.get('input_tokens') if token_usage else None
        output_tokens = token_usage.get('output_tokens') if token_usage else None
        total_tokens = token_usage.get('total_tokens') if token_usage else None
        model_used = token_usage.get('model') if token_usage else None

        # Extract message number from metadata if available
        message_number = parsed.message_number
        if not message_number and hasattr(parsed, 'raw_payload'):
            message_number = parsed.raw_payload.get('metadata', {}).get('message_number')

        return {
            'message_id': parsed.message_id,
            'context_id': context_id,
            'task_id': task_id,
            'interaction_id': interaction_id,
            'timestamp': parsed.timestamp.isoformat(),
            'message_kind': message_kind,
            'is_final': DataMapper.extract_is_final(parsed),
            'message_number': message_number,
            'role': parsed.role.value if parsed.role else 'system',
            'agent_name': parsed.agent_name,
            'sender_id': None,  # Not available in current parsed message
            'feedback_id': getattr(parsed, 'feedback_id', None),
            'correlation_id': DataMapper.extract_correlation_id(parsed),
            'topic': parsed.topic,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'total_tokens': total_tokens,
            'model_used': model_used,
            # JSONB columns for complete data storage
            'message_content': parsed.message_parts,  # The message parts array
            'user_context': {
                'user_profile': {
                    'id': parsed.user_profile.id if parsed.user_profile else None,
                    'name': parsed.user_profile.name if parsed.user_profile else None,
                    'email': parsed.user_profile.email if parsed.user_profile else None,
                    'company': parsed.user_profile.company if parsed.user_profile else None,
                    'location': parsed.user_profile.location if parsed.user_profile else None,
                    'country': parsed.user_profile.country if parsed.user_profile else None,
                    'authenticated': parsed.user_profile.authenticated if parsed.user_profile else None,
                }
            } if parsed.user_profile else None,
            'status_data': None,  # Will be populated separately if needed
            'request_data': None,  # Will be populated separately if needed
            'message_payload': None,  # Full payload if needed
            'metadata': {
                'agent_id': parsed.agent_id,
                'message_type': MessageTypeDetector.detect(parsed),
                'is_partial': is_partial,
                'function_call_id': parsed.function_call_id,
                'method': parsed.method
            }
        }

    @staticmethod
    def extract_tool_calls(parsed: ParsedMessage) -> Optional[List[Dict[str, Any]]]:
        """
        Extract tool calls from ParsedMessage and return as JSONB-ready array.
        Consolidates data from multiple sources into a single structure.
        """
        tool_calls = []
        tool_result_map = {}

        # Step 1: Create mapping of function_call_id to tool results
        for result in parsed.tool_results:
            if result.get('type') == 'tool_result':
                func_call_id = result.get('function_call_id')
                if func_call_id:
                    tool_result_map[func_call_id] = {
                        'result_data': result.get('result_data', {}),
                        'tool_name': result.get('tool_name')
                    }

        # Step 2: Extract tool invocations from parsed.tool_calls
        function_calls = []

        # Handle tool_invocation_start type
        for tool_call in parsed.tool_calls:
            call_type = tool_call.get('type')

            if call_type == 'tool_invocation_start':
                function_calls.append({
                    'id': tool_call.get('function_call_id'),
                    'name': tool_call.get('tool_name'),
                    'args': tool_call.get('tool_args', {})
                })

        # Also extract function_call objects from llm_response parts
        for part in parsed.message_parts:
            if part.get('kind') == 'data':
                data = part.get('data', {})
                if data.get('type') == 'llm_response':
                    response_data = data.get('data', {})
                    content = response_data.get('content', {})
                    parts = content.get('parts', [])

                    # Extract function_call from parts
                    for p in parts:
                        if 'function_call' in p:
                            func_call = p['function_call']
                            function_calls.append({
                                'id': func_call.get('id'),
                                'name': func_call.get('name'),
                                'args': func_call.get('args', {})
                            })

        # Step 3: Build tool call records with results if available
        for func_call in function_calls:
            func_call_id = func_call.get('id')
            tool_name = func_call.get('name')

            tool_data = {
                'tool_name': tool_name,
                'function_call_id': func_call_id,
                'parameters': func_call.get('args', {}),
                'status': 'called',
                'result': None,
                'timestamp': parsed.timestamp.isoformat()
            }

            # If we have a result for this call, update it
            if func_call_id and func_call_id in tool_result_map:
                result_info = tool_result_map[func_call_id]
                tool_data['result'] = result_info['result_data']
                tool_data['status'] = 'success'

            tool_calls.append(tool_data)

        return tool_calls if tool_calls else None


class ConversationManager:
    """Manages conversations via TEXT context_id"""

    def __init__(self, client: Client, cache: DatabaseCache):
        self.client = client
        self.cache = cache

    def ensure_exists(self, parsed: ParsedMessage) -> str:
        """Ensure conversation exists using UPSERT (atomic, no race condition)"""
        context_id = parsed.context_id
        if not context_id:
            raise ValueError("Missing context_id")
        if self.cache.has_conversation(context_id):
            return context_id

        # UPSERT: Insert if not exists, do nothing if exists (atomic operation)
        # This eliminates race conditions and exception-based error handling
        self.client.table('conversations').upsert(
            DataMapper.to_conversation(parsed),
            on_conflict='context_id'
        ).execute()

        self.cache.cache_conversation(context_id)
        return context_id

    def update_stats(self, parsed: ParsedMessage, context_id: str) -> None:
        """Update conversation statistics using atomic increments to avoid race conditions"""
        try:
            # Call Postgres function for atomic increment
            params = {
                'p_context_id': context_id,
                'p_message_increment': 1,
                'p_token_increment': parsed.token_usage.total_tokens if parsed.token_usage else 0,
                'p_input_token_increment': parsed.token_usage.input_tokens if parsed.token_usage else 0,
                'p_output_token_increment': parsed.token_usage.output_tokens if parsed.token_usage else 0,
                'p_cached_token_increment': parsed.token_usage.cached_tokens if parsed.token_usage else 0,
                'p_ended_at': parsed.timestamp.isoformat()
            }
            self.client.rpc('increment_conversation_stats', params).execute()
            # print(f"✓ Atomic increment successful for {context_id}")  # Debug
        except Exception as e:
            # Fallback to read-modify-write if function doesn't exist
            # This can happen if the database function hasn't been created yet
            error_str = str(e)
            print(f"⚠️  RPC failed, using fallback for {context_id}: {e}")  # Debug
            if 'function' in error_str.lower() and 'does not exist' in error_str.lower():
                # Function doesn't exist, use fallback
                self._update_stats_fallback(parsed, context_id)
            else:
                # Other error, use fallback too (to keep system running)
                self._update_stats_fallback(parsed, context_id)

    def _update_stats_fallback(self, parsed: ParsedMessage, context_id: str) -> None:
        """Fallback method using read-modify-write (has race condition, but works)"""
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


class MessageManager:
    """Manages message records in Supabase (consolidated schema with JSONB columns)"""

    def __init__(self, client: Client):
        self.client = client

    def insert(self, parsed: ParsedMessage, context_id: str, task_id: Optional[str] = None, interaction_id: Optional[str] = None) -> tuple[str, bool]:
        """
        Insert message into database with consolidated schema.
        Tool calls and task data are now stored as JSONB in the messages table.
        Returns (message_id, is_new) tuple where is_new indicates if the message was newly inserted.
        """
        if not parsed.message_id:
            parsed.message_id = f"auto-{parsed.id}-{int(parsed.timestamp.timestamp())}"

        # Get base message data
        msg = DataMapper.to_message(parsed, context_id, task_id, interaction_id)

        # Add tool calls as JSONB column
        msg['tool_calls'] = DataMapper.extract_tool_calls(parsed)

        try:
            self.client.table('messages').insert(msg).execute()
            return parsed.message_id, True
        except Exception as e:
            # Check if it's a duplicate key error
            error_dict = str(e)
            if '23505' in error_dict or 'duplicate key' in error_dict.lower():
                # Message already exists, return existing ID
                return parsed.message_id, False
            else:
                # Re-raise other errors
                raise


class InteractionManager:
    """Manages interaction records (user query → agent response pairs)"""

    def __init__(self, client: Client):
        self.client = client
        self._interaction_cache: Dict[str, Dict[str, Any]] = {}

    def get_or_create_interaction(self, parsed: ParsedMessage, context_id: str) -> str:
        """
        Get or create an interaction record.
        Returns the interaction_id (main task ID).
        """
        # Interaction ID is the main task ID (without parent)
        interaction_id = self._determine_interaction_id(parsed)

        if not interaction_id:
            return None

        # Check cache first
        if interaction_id in self._interaction_cache:
            return interaction_id

        # Check if exists in database
        resp = self.client.table('interactions').select('interaction_id').eq('interaction_id', interaction_id).execute()

        if not resp.data:
            # Create new interaction
            self._create_interaction(parsed, context_id, interaction_id)

        # Cache it
        self._interaction_cache[interaction_id] = {'created': True}
        return interaction_id

    def _determine_interaction_id(self, parsed: ParsedMessage) -> Optional[str]:
        """
        Determine the main interaction ID from the message.
        This is the main task ID (gdk-task-XXX) that initiated the user query.
        """
        # If this message has a parent_task_id, that's the interaction_id
        if parsed.parent_task_id:
            return parsed.parent_task_id

        # If no parent, this IS the main task, so use its ID
        # But only if it's actually a main task (starts with gdk-task-)
        if parsed.id and parsed.id.startswith('gdk-task-'):
            return parsed.id

        return None

    def _create_interaction(self, parsed: ParsedMessage, context_id: str, interaction_id: str) -> None:
        """Create a new interaction record"""
        # Calculate interaction number (how many interactions in this conversation so far)
        resp = self.client.table('interactions').select('interaction_number').eq('context_id', context_id).order('interaction_number', desc=True).limit(1).execute()

        interaction_number = 1
        if resp.data:
            interaction_number = resp.data[0].get('interaction_number', 0) + 1

        interaction_data = {
            'interaction_id': interaction_id,
            'context_id': context_id,
            'interaction_number': interaction_number,
            'started_at': parsed.timestamp.isoformat(),
            'primary_agent': parsed.agent_name,
            'response_state': 'in_progress',
            'metadata': {}
        }

        # If this is a user message, capture the query
        if parsed.role == MessageRole.USER:
            interaction_data['user_message_id'] = parsed.message_id
            interaction_data['user_query'] = parsed.message_text
            interaction_data['user_query_timestamp'] = parsed.timestamp.isoformat()

        try:
            self.client.table('interactions').insert(interaction_data).execute()
        except Exception as e:
            error_str = str(e)
            # Check if it's a duplicate key error (race condition)
            if '23505' in error_str or 'duplicate key' in error_str.lower():
                # Interaction was created by another thread, this is fine
                print(f"ℹ️  Interaction {interaction_id[:24]}... already exists (race condition, message_id: {parsed.message_id})")
            else:
                # Some other error, re-raise
                raise

    def update_interaction(self, parsed: ParsedMessage, interaction_id: str) -> None:
        """Update interaction with response, metrics, or completion status"""
        if not interaction_id:
            return

        update_data = {}

        # Update user query ONLY if this is from the main task (not a subtask delegation)
        # The main task's ID equals the interaction_id
        if parsed.role == MessageRole.USER and parsed.id == interaction_id:
            update_data['user_message_id'] = parsed.message_id
            update_data['user_query'] = parsed.message_text
            update_data['user_query_timestamp'] = parsed.timestamp.isoformat()

        # Update agent response ONLY if this is the main task's final response (not subtask response)
        # The main task's ID equals the interaction_id
        if parsed.role == MessageRole.AGENT and parsed.task_status == TaskStatus.COMPLETED and parsed.id == interaction_id:
            update_data['agent_response_message_id'] = parsed.message_id
            update_data['agent_response'] = parsed.message_text
            update_data['agent_response_timestamp'] = parsed.timestamp.isoformat()
            update_data['response_state'] = 'completed'
            update_data['completed_at'] = parsed.timestamp.isoformat()

        # Update token usage
        if parsed.token_usage:
            # Get current totals first
            resp = self.client.table('interactions').select('total_tokens,total_input_tokens,total_output_tokens,total_cached_tokens').eq('interaction_id', interaction_id).execute()

            if resp.data:
                current = resp.data[0]
                update_data['total_tokens'] = current.get('total_tokens', 0) + parsed.token_usage.total_tokens
                update_data['total_input_tokens'] = current.get('total_input_tokens', 0) + parsed.token_usage.input_tokens
                update_data['total_output_tokens'] = current.get('total_output_tokens', 0) + parsed.token_usage.output_tokens
                update_data['total_cached_tokens'] = current.get('total_cached_tokens', 0) + parsed.token_usage.cached_tokens

        # Track delegated agents
        if parsed.agent_name and parsed.parent_task_id:
            # This is a subtask, add agent to delegated_agents array
            resp = self.client.table('interactions').select('delegated_agents,primary_agent').eq('interaction_id', interaction_id).execute()

            if resp.data:
                current_delegated = resp.data[0].get('delegated_agents', []) or []
                primary = resp.data[0].get('primary_agent')

                # Only add if not primary and not already in list
                if parsed.agent_name != primary and parsed.agent_name not in current_delegated:
                    current_delegated.append(parsed.agent_name)
                    update_data['delegated_agents'] = current_delegated

        # Update subtask count if this is a subtask
        if parsed.parent_task_id:
            resp = self.client.table('interactions').select('num_subtasks').eq('interaction_id', interaction_id).execute()
            if resp.data:
                update_data['num_subtasks'] = resp.data[0].get('num_subtasks', 0) + 1

        # Update tool call count
        if parsed.tool_calls or parsed.tool_results:
            num_tool_calls = len(parsed.tool_calls) + len(parsed.tool_results)
            resp = self.client.table('interactions').select('num_tool_calls').eq('interaction_id', interaction_id).execute()
            if resp.data:
                update_data['num_tool_calls'] = resp.data[0].get('num_tool_calls', 0) + num_tool_calls

        # Increment message count
        resp = self.client.table('interactions').select('total_messages').eq('interaction_id', interaction_id).execute()
        if resp.data:
            update_data['total_messages'] = resp.data[0].get('total_messages', 0) + 1

        if update_data:
            self.client.table('interactions').update(update_data).eq('interaction_id', interaction_id).execute()


class SupabaseUploader:
    """Main uploader class that coordinates all managers for consolidated schema"""

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
        self.message_manager = MessageManager(self.client)
        self.interaction_manager = InteractionManager(self.client)

    def upload_message(self, parsed: ParsedMessage) -> Dict[str, str]:
        """
        Upload a parsed message to Supabase using consolidated schema.
        Tool calls and task data are now stored within the message record.
        Returns dict with created record IDs.
        """
        result = {}

        try:
            if not parsed.context_id:
                return {'error': 'Missing context_id'}

            # Ensure conversation exists
            ctx = self.conversation_manager.ensure_exists(parsed)
            result['context_id'] = ctx

            # Get or create interaction (user query → response pair)
            interaction_id = self.interaction_manager.get_or_create_interaction(parsed, ctx)
            result['interaction_id'] = interaction_id

            # Task ID is the parsed message ID
            t_id = parsed.id
            result['task_id'] = t_id

            # Insert message with consolidated data (includes tool_calls in JSONB)
            m_id, is_new = self.message_manager.insert(parsed, ctx, t_id, interaction_id)
            result['message_id'] = m_id
            result['message_is_new'] = is_new

            # Only update stats if this is a new message
            if is_new:
                self.conversation_manager.update_stats(parsed, ctx)

                # Update interaction metrics
                if interaction_id:
                    self.interaction_manager.update_interaction(parsed, interaction_id)

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
