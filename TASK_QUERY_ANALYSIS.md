# Task Query Analysis - Tool Calls, Reasoning, Inputs & Outputs

## Overview
This document explains how to query tool calls, reasoning (thinking blocks), tool inputs, and tool outputs by task ID and subtask ID in the SAM Feedback Listener system.

## Data Storage Structure

### Database Tables
- **conversations**: Top-level table, keyed by `context_id` (e.g., `web-session-XXX`)
- **interactions**: Mid-level table, keyed by `interaction_id` (main task ID, e.g., `gdk-task-XXX`), references `context_id`
- **messages**: Low-level table, contains all message data, references both `context_id` and `interaction_id`

### Key JSONB Fields in Messages Table

| Field | Content | Purpose |
|-------|---------|---------|
| `message_content` | Array of message parts from `payload.result.status.message.parts` | Contains LLM responses, function calls, text, thinking blocks |
| `tool_calls` | Extracted and flattened tool calls | Quick access to tool invocations and results |
| `raw_payload` | Complete original JSON message | Full audit trail |
| `user_context_raw` | User profile data | User information with original field names |
| `token_usage_raw` | Token usage data | Input/output tokens |

## Data Structures

### Tool Calls Structure (in `tool_calls` JSONB field)

Tool calls are extracted and stored with the following structure:

#### Tool Invocation (type: `tool_invocation_start`)
```json
{
  "type": "tool_invocation_start",
  "id": "tooluse_XXXXX",
  "name": "peer_JDE_HR_Agent",
  "args": {
    "task_description": "...",
    "user_query": "..."
  },
  "timestamp": "2025-10-26T15:31:53.039100"
}
```

#### LLM Response with Function Call (type: `llm_response`)
```json
{
  "type": "llm_response",
  "id": "tooluse_XXXXX",
  "name": "create_chart_from_plotly_config",
  "args": {
    "config_content": "...",
    "output_format": "png"
  },
  "timestamp": "2025-10-26T15:31:47.659085"
}
```

#### Tool Result (type: `tool_result`)
```json
{
  "type": "tool_result",
  "id": "tooluse_XXXXX",
  "name": "create_chart_from_plotly_config",
  "result": {
    "status": "success",
    "message": "Chart created successfully"
  },
  "timestamp": "2025-10-26T15:31:57.111400"
}
```

### Message Parts Structure (in `message_content` JSONB field)

The `message_content` field contains an array of parts from the original message. Each part can be:

#### Text Part
```json
{
  "kind": "text",
  "text": "I'll help you find information..."
}
```

#### Data Part with LLM Response
```json
{
  "kind": "data",
  "data": {
    "type": "llm_response",
    "data": {
      "content": {
        "parts": [
          {
            "text": "Response text here"
          },
          {
            "function_call": {
              "id": "tooluse_XXXXX",
              "name": "tool_name",
              "args": { ... }
            }
          }
        ],
        "role": "model"
      },
      "usage_metadata": {
        "prompt_token_count": 7667,
        "candidates_token_count": 153
      }
    }
  }
}
```

#### Data Part with LLM Invocation (contains thinking/reasoning)
```json
{
  "kind": "data",
  "data": {
    "type": "llm_invocation",
    "request": {
      "model": "openai/bedrock-claude-4-5-sonnet-tools",
      "contents": [
        {
          "role": "user",
          "parts": [{ "text": "..." }]
        },
        {
          "role": "model",
          "parts": [{ "text": "..." }]
        }
      ]
    }
  }
}
```

**Note**: Thinking/reasoning blocks are embedded in the conversation history within LLM invocations.

## Task and Subtask Relationships

### Task ID Patterns
- **Main Task (Interaction)**: `gdk-task-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX`
- **Subtask**: `a2a_subtask_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX`
- **Context (Conversation)**: `web-session-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX`

### Identifying Subtasks
Subtasks are identified by:
1. `task_id` field in messages table starts with `a2a_subtask_`
2. `delegating_agent_name` in message metadata indicates which agent created the subtask
3. `interaction_id` references the parent task

## SQL Query Examples

### 1. Get All Tool Calls for a Task ID

```sql
SELECT
  message_id,
  timestamp,
  agent_name,
  jsonb_array_elements(tool_calls) as tool_call
FROM messages
WHERE interaction_id = 'gdk-task-6bca9b3773214eaebe69b1f2f3c61c94'
AND tool_calls IS NOT NULL
ORDER BY timestamp;
```

### 2. Get Tool Calls with Inputs and Outputs

```sql
WITH tool_invocations AS (
  SELECT
    message_id,
    timestamp,
    agent_name,
    jsonb_array_elements(tool_calls) as tool_call
  FROM messages
  WHERE interaction_id = 'gdk-task-6bca9b3773214eaebe69b1f2f3c61c94'
  AND tool_calls IS NOT NULL
),
calls_with_results AS (
  SELECT
    tool_call->>'id' as call_id,
    tool_call->>'name' as tool_name,
    tool_call->>'type' as call_type,
    tool_call->'args' as input_args,
    tool_call->'result' as output_result,
    timestamp,
    agent_name
  FROM tool_invocations
)
SELECT
  call_id,
  tool_name,
  call_type,
  jsonb_pretty(input_args) as inputs,
  jsonb_pretty(output_result) as outputs,
  timestamp
FROM calls_with_results
ORDER BY timestamp;
```

### 3. Get Reasoning/Thinking Blocks

```sql
SELECT
  message_id,
  timestamp,
  agent_name,
  part_data
FROM messages,
LATERAL jsonb_array_elements(message_content) as part_data
WHERE interaction_id = 'gdk-task-6bca9b3773214eaebe69b1f2f3c61c94'
AND part_data->'data'->>'type' = 'llm_invocation'
ORDER BY timestamp;
```

### 4. Get All Subtasks for a Main Task

```sql
SELECT DISTINCT
  task_id,
  agent_name,
  COUNT(*) as num_messages,
  MIN(timestamp) as started_at,
  MAX(timestamp) as ended_at
FROM messages
WHERE interaction_id = 'gdk-task-6bca9b3773214eaebe69b1f2f3c61c94'
AND task_id LIKE 'a2a_subtask_%'
GROUP BY task_id, agent_name
ORDER BY started_at;
```

### 5. Complete Task Flow with Tool Calls

```sql
SELECT
  m.timestamp,
  m.task_id,
  m.agent_name,
  CASE
    WHEN m.tool_calls IS NOT NULL THEN 'tool_call'
    WHEN m.message_content IS NOT NULL THEN 'message'
    ELSE 'other'
  END as message_type,
  m.tool_calls,
  m.message_content->0->>'text' as first_text_content
FROM messages m
WHERE m.interaction_id = 'gdk-task-6bca9b3773214eaebe69b1f2f3c61c94'
ORDER BY m.timestamp;
```

## Example: Analyzing Task `gdk-task-6bca9b3773214eaebe69b1f2f3c61c94`

### Task Summary
- **Context ID**: `web-session-a5d0755d587a458095766ab78ec61732`
- **Interaction ID**: `gdk-task-6bca9b3773214eaebe69b1f2f3c61c94`
- **Number of Tool Calls**: 7
- **Number of Subtasks**: 0 (based on interactions table)

### Sample Tool Call Flow
1. **User asks** about garden leave policy
2. **OrchestratorAgent** decides to delegate to HR agent
3. **Tool Call**: `peer_JDE_HR_Agent` (type: `llm_response`)
4. **Tool Invocation**: `peer_JDE_HR_Agent` starts (type: `tool_invocation_start`)
5. **Subtask Created**: HR agent processes request
6. **Tool Call within Subtask**: `get_decision_trees_hr_decision_trees`
7. **Tool Result**: HR agent returns response
8. **OrchestratorAgent** forwards result to user

## Implementation Recommendations

### Python Query Module

Create a `task_query.py` module with the following functions:

```python
class TaskQueryService:
    def get_tool_calls_by_task(task_id: str) -> List[ToolCall]
    def get_tool_call_with_io(call_id: str) -> ToolCallWithIO
    def get_reasoning_for_task(task_id: str) -> List[ReasoningBlock]
    def get_subtasks(task_id: str) -> List[SubtaskInfo]
    def get_complete_task_flow(task_id: str) -> TaskFlow
```

### Data Classes

```python
@dataclass
class ToolCall:
    id: str
    name: str
    type: str
    args: Dict[str, Any]
    result: Optional[Dict[str, Any]]
    timestamp: datetime
    agent_name: str

@dataclass
class ReasoningBlock:
    message_id: str
    timestamp: datetime
    agent_name: str
    conversation_history: List[Dict[str, Any]]
    model: str

@dataclass
class SubtaskInfo:
    task_id: str
    parent_task_id: str
    agent_name: str
    num_messages: int
    started_at: datetime
    ended_at: datetime
    tool_calls: List[ToolCall]
```

## Key Findings

1. **Tool Calls are Stored Twice**:
   - In extracted `tool_calls` JSONB field for quick access
   - In `message_content` parts array for full context

2. **Reasoning is in LLM Invocations**:
   - Look for parts with `data.type == 'llm_invocation'`
   - The `contents` array contains the full conversation history including thinking

3. **Tool Results are Linked by ID**:
   - Tool invocation and result share the same `id` field
   - Match them using `tool_call->>'id'`

4. **Subtasks Use Different Task IDs**:
   - Subtasks have their own `task_id` (starting with `a2a_subtask_`)
   - But they reference the same `interaction_id` as the parent task

5. **Original Field Names Preserved**:
   - All JSONB fields maintain original field names (no normalization)
   - This ensures flexibility for future schema changes
