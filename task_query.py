"""
Task Query Service - Query tool calls, reasoning, inputs & outputs by task ID

This module provides a high-level interface for querying tool calls, reasoning blocks,
tool inputs/outputs, and subtasks from the Supabase database.

Usage:
    from task_query import TaskQueryService

    service = TaskQueryService()

    # Get all tool calls for a task
    tool_calls = service.get_tool_calls_by_task('gdk-task-XXX')

    # Get complete task flow with reasoning
    flow = service.get_complete_task_flow('gdk-task-XXX')
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any
from supabase import create_client, Client
from dotenv import load_dotenv
import json
import os

# Load environment variables from .env file
load_dotenv()


@dataclass
class ToolCall:
    """Represents a single tool call with inputs and optional outputs"""
    id: str
    name: str
    type: str  # 'tool_invocation_start', 'llm_response', 'tool_result'
    args: Dict[str, Any]
    timestamp: str
    message_id: str
    result: Optional[Dict[str, Any]] = None
    agent_name: Optional[str] = None


@dataclass
class ToolCallWithIO:
    """Tool call with matched input and output"""
    call_id: str
    tool_name: str
    invocation_timestamp: str
    result_timestamp: Optional[str]
    inputs: Dict[str, Any]
    outputs: Optional[Dict[str, Any]]
    agent_name: str
    duration_seconds: Optional[float] = None


@dataclass
class ReasoningBlock:
    """Represents reasoning/thinking extracted from LLM invocations"""
    message_id: str
    timestamp: str
    agent_name: Optional[str]
    model: str
    conversation_history: List[Dict[str, Any]]
    request_data: Dict[str, Any]


@dataclass
class SubtaskInfo:
    """Information about a subtask"""
    task_id: str
    parent_task_id: str
    agent_name: Optional[str]
    num_messages: int
    started_at: str
    ended_at: str
    tool_calls: List[ToolCall] = field(default_factory=list)


@dataclass
class TaskFlow:
    """Complete flow of a task including all messages, tool calls, and subtasks"""
    task_id: str
    context_id: str
    started_at: str
    completed_at: Optional[str]
    total_messages: int
    tool_calls: List[ToolCallWithIO]
    subtasks: List[SubtaskInfo]
    reasoning_blocks: List[ReasoningBlock]


class TaskQueryService:
    """Service for querying task data from Supabase"""

    def __init__(self, supabase_url: Optional[str] = None, supabase_key: Optional[str] = None):
        """
        Initialize the TaskQueryService

        Args:
            supabase_url: Supabase project URL (or reads from SUPABASE_URL env var)
            supabase_key: Supabase API key (or reads from SUPABASE_KEY env var)
        """
        url = supabase_url or os.getenv('SUPABASE_URL')
        key = supabase_key or os.getenv('SUPABASE_KEY')

        if not url or not key:
            raise ValueError(
                "Supabase credentials required. "
                "Provide via parameters or set SUPABASE_URL and SUPABASE_KEY environment variables."
            )

        self.client: Client = create_client(url, key)

    def get_tool_calls_by_task(self, task_id: str) -> List[ToolCall]:
        """
        Get all tool calls for a given task ID (interaction_id or task_id)

        Args:
            task_id: The task ID to query (can be interaction_id like 'gdk-task-XXX'
                    or subtask task_id like 'a2a_subtask-XXX')

        Returns:
            List of ToolCall objects sorted by timestamp
        """
        # Determine if this is a main task or subtask
        if task_id.startswith('gdk-task-'):
            query = self.client.table('messages').select('*').eq('interaction_id', task_id)
        else:
            query = self.client.table('messages').select('*').eq('task_id', task_id)

        response = query.not_.is_('tool_calls', 'null').order('timestamp').execute()

        tool_calls = []
        for msg in response.data:
            if not msg.get('tool_calls'):
                continue

            for tc in msg['tool_calls']:
                tool_calls.append(ToolCall(
                    id=tc.get('id', ''),
                    name=tc.get('name', ''),
                    type=tc.get('type', ''),
                    args=tc.get('args', {}),
                    timestamp=tc.get('timestamp', msg['timestamp']),
                    message_id=msg['message_id'],
                    result=tc.get('result'),
                    agent_name=msg.get('agent_name')
                ))

        return tool_calls

    def get_tool_calls_with_io(self, task_id: str) -> List[ToolCallWithIO]:
        """
        Get tool calls matched with their inputs and outputs

        Args:
            task_id: The task ID to query

        Returns:
            List of ToolCallWithIO objects with matched inputs/outputs
        """
        tool_calls = self.get_tool_calls_by_task(task_id)

        # Group by call_id to match invocations with results
        calls_by_id: Dict[str, Dict[str, Any]] = {}

        for tc in tool_calls:
            if tc.id not in calls_by_id:
                calls_by_id[tc.id] = {
                    'invocation': None,
                    'result': None
                }

            if tc.type in ['tool_invocation_start', 'llm_response']:
                calls_by_id[tc.id]['invocation'] = tc
            elif tc.type == 'tool_result':
                calls_by_id[tc.id]['result'] = tc

        # Build ToolCallWithIO objects
        matched_calls = []
        for call_id, data in calls_by_id.items():
            inv = data['invocation']
            res = data['result']

            if not inv:
                continue

            # Calculate duration if we have both timestamps
            duration = None
            if res:
                try:
                    inv_time = datetime.fromisoformat(inv.timestamp.replace('Z', '+00:00'))
                    res_time = datetime.fromisoformat(res.timestamp.replace('Z', '+00:00'))
                    duration = (res_time - inv_time).total_seconds()
                except:
                    pass

            matched_calls.append(ToolCallWithIO(
                call_id=call_id,
                tool_name=inv.name,
                invocation_timestamp=inv.timestamp,
                result_timestamp=res.timestamp if res else None,
                inputs=inv.args,
                outputs=res.result if res else None,
                agent_name=inv.agent_name or '',
                duration_seconds=duration
            ))

        # Sort by invocation timestamp
        matched_calls.sort(key=lambda x: x.invocation_timestamp)
        return matched_calls

    def get_reasoning_blocks(self, task_id: str) -> List[ReasoningBlock]:
        """
        Extract reasoning/thinking blocks from LLM invocations

        Args:
            task_id: The task ID to query

        Returns:
            List of ReasoningBlock objects containing conversation history
        """
        # Determine if this is a main task or subtask
        if task_id.startswith('gdk-task-'):
            query = self.client.table('messages').select('*').eq('interaction_id', task_id)
        else:
            query = self.client.table('messages').select('*').eq('task_id', task_id)

        response = query.not_.is_('message_content', 'null').order('timestamp').execute()

        reasoning_blocks = []

        for msg in response.data:
            if not msg.get('message_content'):
                continue

            # Look for LLM invocations in message parts
            for part in msg['message_content']:
                if isinstance(part, dict) and part.get('data', {}).get('type') == 'llm_invocation':
                    request_data = part['data'].get('request', {})

                    reasoning_blocks.append(ReasoningBlock(
                        message_id=msg['message_id'],
                        timestamp=msg['timestamp'],
                        agent_name=msg.get('agent_name'),
                        model=request_data.get('model', 'unknown'),
                        conversation_history=request_data.get('contents', []),
                        request_data=request_data
                    ))

        return reasoning_blocks

    def get_subtasks(self, parent_task_id: str) -> List[SubtaskInfo]:
        """
        Get all subtasks for a parent task

        Args:
            parent_task_id: The parent task ID (interaction_id)

        Returns:
            List of SubtaskInfo objects
        """
        # Query all messages for this interaction that have a2a_subtask task_ids
        response = (
            self.client.table('messages')
            .select('*')
            .eq('interaction_id', parent_task_id)
            .like('task_id', 'a2a_subtask_%')
            .order('timestamp')
            .execute()
        )

        # Group by task_id
        subtasks_dict: Dict[str, List[Dict]] = {}
        for msg in response.data:
            task_id = msg.get('task_id')
            if task_id and task_id.startswith('a2a_subtask_'):
                if task_id not in subtasks_dict:
                    subtasks_dict[task_id] = []
                subtasks_dict[task_id].append(msg)

        # Build SubtaskInfo objects
        subtasks = []
        for task_id, messages in subtasks_dict.items():
            messages.sort(key=lambda m: m['timestamp'])

            # Extract tool calls for this subtask
            tool_calls_for_subtask = []
            for msg in messages:
                if msg.get('tool_calls'):
                    for tc in msg['tool_calls']:
                        tool_calls_for_subtask.append(ToolCall(
                            id=tc.get('id', ''),
                            name=tc.get('name', ''),
                            type=tc.get('type', ''),
                            args=tc.get('args', {}),
                            timestamp=tc.get('timestamp', msg['timestamp']),
                            message_id=msg['message_id'],
                            result=tc.get('result'),
                            agent_name=msg.get('agent_name')
                        ))

            subtasks.append(SubtaskInfo(
                task_id=task_id,
                parent_task_id=parent_task_id,
                agent_name=messages[0].get('agent_name'),
                num_messages=len(messages),
                started_at=messages[0]['timestamp'],
                ended_at=messages[-1]['timestamp'],
                tool_calls=tool_calls_for_subtask
            ))

        return subtasks

    def get_complete_task_flow(self, task_id: str) -> TaskFlow:
        """
        Get complete task flow including tool calls, subtasks, and reasoning

        Args:
            task_id: The task ID to query (interaction_id)

        Returns:
            TaskFlow object with complete task information
        """
        # Get interaction metadata
        interaction_response = (
            self.client.table('interactions')
            .select('*')
            .eq('interaction_id', task_id)
            .execute()
        )

        if not interaction_response.data:
            raise ValueError(f"Task {task_id} not found in interactions table")

        interaction = interaction_response.data[0]

        # Get all components
        tool_calls = self.get_tool_calls_with_io(task_id)
        subtasks = self.get_subtasks(task_id)
        reasoning = self.get_reasoning_blocks(task_id)

        # Count total messages
        message_count_response = (
            self.client.table('messages')
            .select('message_id', count='exact')
            .eq('interaction_id', task_id)
            .execute()
        )

        return TaskFlow(
            task_id=task_id,
            context_id=interaction['context_id'],
            started_at=interaction['started_at'],
            completed_at=interaction.get('completed_at'),
            total_messages=message_count_response.count or 0,
            tool_calls=tool_calls,
            subtasks=subtasks,
            reasoning_blocks=reasoning
        )

    def print_task_summary(self, task_id: str):
        """
        Print a human-readable summary of a task

        Args:
            task_id: The task ID to summarize
        """
        flow = self.get_complete_task_flow(task_id)

        print(f"\n{'='*80}")
        print(f"Task Summary: {task_id}")
        print(f"{'='*80}")
        print(f"Context ID: {flow.context_id}")
        print(f"Started: {flow.started_at}")
        print(f"Completed: {flow.completed_at or 'In Progress'}")
        print(f"Total Messages: {flow.total_messages}")
        print(f"Tool Calls: {len(flow.tool_calls)}")
        print(f"Subtasks: {len(flow.subtasks)}")
        print(f"Reasoning Blocks: {len(flow.reasoning_blocks)}")

        print(f"\n{'-'*80}")
        print("Tool Calls:")
        print(f"{'-'*80}")
        for i, tc in enumerate(flow.tool_calls, 1):
            status = "✓" if tc.outputs else "⧖"
            duration = f" ({tc.duration_seconds:.2f}s)" if tc.duration_seconds else ""
            print(f"{i}. {status} {tc.tool_name}{duration}")
            print(f"   Agent: {tc.agent_name}")
            print(f"   Invoked: {tc.invocation_timestamp}")
            print(f"   Inputs: {json.dumps(tc.inputs, indent=4)}")
            if tc.outputs:
                print(f"   Outputs: {json.dumps(tc.outputs, indent=4)}")
            print()

        if flow.subtasks:
            print(f"\n{'-'*80}")
            print("Subtasks:")
            print(f"{'-'*80}")
            for i, subtask in enumerate(flow.subtasks, 1):
                print(f"{i}. {subtask.task_id}")
                print(f"   Agent: {subtask.agent_name}")
                print(f"   Messages: {subtask.num_messages}")
                print(f"   Tool Calls: {len(subtask.tool_calls)}")
                print(f"   Duration: {subtask.started_at} to {subtask.ended_at}")
                print()

        if flow.reasoning_blocks:
            print(f"\n{'-'*80}")
            print(f"Reasoning Blocks: {len(flow.reasoning_blocks)}")
            print(f"{'-'*80}")
            for i, rb in enumerate(flow.reasoning_blocks, 1):
                print(f"{i}. Model: {rb.model}, Agent: {rb.agent_name}")
                print(f"   Timestamp: {rb.timestamp}")
                print(f"   Conversation turns: {len(rb.conversation_history)}")
                print()

        print(f"{'='*80}\n")


# Example usage
if __name__ == "__main__":
    import sys

    # Example: python task_query.py gdk-task-6bca9b3773214eaebe69b1f2f3c61c94

    if len(sys.argv) < 2:
        print("Usage: python task_query.py <task_id>")
        print("Example: python task_query.py gdk-task-6bca9b3773214eaebe69b1f2f3c61c94")
        sys.exit(1)

    task_id = sys.argv[1]

    try:
        service = TaskQueryService()
        service.print_task_summary(task_id)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
