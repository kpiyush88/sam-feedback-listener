# SAM Feedback Listener - Database Schema Documentation

**Version**: 2.2
**Last Updated**: 2025-11-01
**Database**: Supabase PostgreSQL

---

## Overview

The database uses a **single source of truth** design with the `messages` table as the core entity. All aggregated views (conversations, interactions) are derived from this table using SQL views.

### Design Philosophy

- **Single Table Design**: Only `messages` table stores data
- **Derived Views**: `conversations_derived`, `interactions_derived`, and `tool_interactions_view` computed on-demand from messages
- **Always Accurate**: No synchronization bugs or stale data
- **JSONB Flexibility**: Complete raw data preserved for schema evolution
- **Analytics Ready**: Specialized views for conversation, interaction, and tool call analysis

### Database Structure

```
messages (BASE TABLE)
   ↓ derives
   ├─ conversations_derived (VIEW)
   ├─ interactions_derived (VIEW)
   └─ tool_interactions_view (VIEW)
```

---

## Table: `messages`

The only physical table - stores all message data with complete JSONB storage of raw payloads.

### Columns

#### Indexed/Core Fields

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `message_id` | text | NO | - | **Primary Key**. Unique message identifier |
| `context_id` | text | YES | - | Conversation session ID (e.g., `web-session-16644981729f49ec8d4a6c19ce719f74`) |
| `task_id` | text | YES | - | **Actual task ID** of this message. For main tasks: `gdk-task-*`, for subtasks: `a2a_subtask_*`. |
| `timestamp` | timestamp with time zone | NO | - | When the message was created |
| `role` | text | YES | - | Message role: `user`, `agent`, `system` |
| `agent_name` | text | YES | - | Name of the agent if role is `agent` |
| `topic` | text | YES | - | Message topic path (e.g., `jde-sam-test/a2a/v1/agent/status/OrchestratorAgent/...`) |

#### JSONB Columns (Flexible Data Storage)

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `message_content` | jsonb | YES | **Raw message parts array** - The message payload structure as-is from original |
| `tool_calls` | jsonb | YES | **Tool invocations and results** - Array of tool calls with original field names preserved |
| `raw_payload` | jsonb | YES | **Complete original payload** - Full raw message payload from upstream |
| `user_context_raw` | jsonb | YES | **Raw user profile** - Complete user profile with original field names |
| `token_usage_raw` | jsonb | YES | **Raw token usage data** - Token usage data as-is from original (may have variant field names) |
| `metadata` | jsonb | YES | Additional metadata (task_status, message_type, method, message_number, etc.) |

#### System Fields

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `created_at` | timestamp with time zone | YES | now() | Record creation timestamp |
| `updated_at` | timestamp with time zone | YES | now() | Record last update timestamp |

### Indexes

- `message_id` (PRIMARY KEY)
- `context_id` (for conversation queries)
- `task_id` (for task/subtask queries)

### Task Hierarchy

Task delegation is tracked via `task_id`:

**Main Task Messages:**
- `task_id` = `gdk-task-abc123` (the main task)

**Subtask Messages:**
- `task_id` = `a2a_subtask_xyz789` (the specific subtask)

**Example Query - Get all messages for a task:**
```sql
SELECT * FROM messages
WHERE task_id = 'gdk-task-abc123'
ORDER BY timestamp;
```

**Example Query - Get all subtasks in a context:**
```sql
SELECT task_id, COUNT(*) as message_count
FROM messages
WHERE context_id = 'web-session-xxx'
  AND task_id LIKE 'a2a_subtask_%'
GROUP BY task_id;
```

---

## View: `conversations_derived`

Derived view that aggregates conversation-level statistics from messages.

### Columns

| Column | Type | Description |
|--------|------|-------------|
| `context_id` | text | Primary identifier for the conversation |
| `started_at` | timestamp with time zone | Earliest message timestamp in conversation |
| `ended_at` | timestamp with time zone | Latest message timestamp in conversation |
| `total_messages` | bigint | Count of all messages in conversation |
| `user_id` | text | User identifier (from first message with user_context_raw) |
| `user_name` | text | User display name |
| `user_country` | text | User's country |
| `user_context_raw` | jsonb | Complete user profile with original field names |
| `created_at` | timestamp with time zone | Same as started_at |
| `updated_at` | timestamp with time zone | Same as ended_at |

### Example Query

```sql
SELECT * FROM conversations_derived
WHERE user_id = 'user@example.com'
ORDER BY started_at DESC;
```

---

## View: `interactions_derived`

Derived view that aggregates interaction-level statistics for main tasks (user query → agent response pairs).

### Columns

| Column | Type | Description |
|--------|------|-------------|
| `interaction_id` | text | Main task ID (same as task_id for main tasks, e.g., `gdk-task-xxx`) |
| `context_id` | text | Reference to conversation |
| `interaction_number` | bigint | Sequential number of this interaction within the conversation |
| `started_at` | timestamp with time zone | Earliest message timestamp for this task |
| `completed_at` | timestamp with time zone | Latest message timestamp where task_status = 'completed' |
| `agent_response_message_id` | text | Last agent message ID for this task |
| `agent_response_timestamp` | timestamp with time zone | Last agent message timestamp |
| `response_state` | text | Task state: `in_progress` or `completed` |
| `primary_agent` | text | First agent that handled this task |
| `total_messages` | bigint | Count of messages in this interaction |
| `created_at` | timestamp with time zone | Same as started_at |
| `updated_at` | timestamp with time zone | Latest message timestamp |

### Example Queries

**Get all interactions for a conversation:**
```sql
SELECT * FROM interactions_derived
WHERE context_id = 'web-session-xxx'
ORDER BY interaction_number;
```

**Get completed interactions:**
```sql
SELECT
    interaction_id,
    primary_agent,
    total_messages,
    completed_at - started_at as duration
FROM interactions_derived
WHERE response_state = 'completed'
ORDER BY started_at DESC;
```

**Get interaction statistics by agent:**
```sql
SELECT
    primary_agent,
    COUNT(*) as total_interactions,
    AVG(total_messages) as avg_messages
FROM interactions_derived
WHERE response_state = 'completed'
GROUP BY primary_agent;
```

---

## View: `tool_interactions_view`

**Purpose**: Analytics view that tracks complete tool call lifecycles at the interaction level, linking all 4 phases: pre-reasoning, LLM decision, tool invocation, and tool result.

**Version**: 1.8
**Added**: 2025-11-01
**Updated**: 2025-11-02 - Fixed multi-tool call handling + conditional pre_reasoning_timestamp

### Overview

This view provides a complete picture of each tool call made by agents, combining:
1. **Pre-reasoning** - Agent's status update before deciding to call a tool (optional)
2. **LLM Decision** - The LLM's response that includes the tool call decision
3. **Tool Invocation** - The actual tool call with input arguments
4. **Tool Result** - The output/result returned by the tool

Each row represents **one complete tool call cycle** with all associated data.

### Linking Strategy

Tool call phases are linked using:
- **task_id + agent_name** - Structural grouping within the same task/agent
- **tool_call_id** - Links phases 2-4 (LLM decision, invocation, result)
- **Sequential ordering** - Pre-reasoning identified by message sequence (LEAD window function)

### Columns

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `interaction_id` | text | NO | Main task ID (gdk-task-*) or context_id fallback |
| `context_id` | text | YES | Conversation session ID |
| `task_id` | text | YES | Actual task ID (main task or subtask) |
| `tool_call_id` | text | YES | Unique identifier for this tool call |
| `tool_name` | text | YES | Name of the tool that was called |
| `agent_name` | text | YES | Agent that made the tool call |
| `pre_reasoning_text` | text | YES | Status text from agent_progress_update before tool decision |
| `pre_reasoning_timestamp` | timestamptz | YES | When the pre-reasoning occurred |
| `llm_decision_content` | jsonb | YES | Complete LLM response including tool decision |
| `llm_decision_timestamp` | timestamptz | YES | When LLM decided to call the tool |
| `tool_input_args` | jsonb | YES | Input arguments passed to the tool |
| `invocation_timestamp` | timestamptz | YES | When tool was invoked |
| `tool_output_result` | jsonb | YES | Result/output returned by the tool |
| `result_timestamp` | timestamptz | YES | When tool result was received |
| `execution_duration_ms` | float | YES | Tool execution time in milliseconds |
| `token_usage` | jsonb | YES | Token usage for this tool call cycle |
| `success_status` | boolean | YES | True if result.status='success', False if 'error', NULL otherwise |

### Example Queries

**Get all tool calls for an interaction:**
```sql
SELECT
    tool_name,
    agent_name,
    pre_reasoning_text,
    execution_duration_ms,
    success_status
FROM tool_interactions_view
WHERE interaction_id = 'gdk-task-abc123'
ORDER BY invocation_timestamp;
```

**Analyze tool performance by agent:**
```sql
SELECT
    agent_name,
    tool_name,
    COUNT(*) as total_calls,
    AVG(execution_duration_ms) as avg_duration_ms,
    COUNT(CASE WHEN success_status = true THEN 1 END) as successful_calls,
    COUNT(pre_reasoning_text) as calls_with_reasoning
FROM tool_interactions_view
GROUP BY agent_name, tool_name
ORDER BY total_calls DESC;
```

**Find slowest tool calls:**
```sql
SELECT
    tool_name,
    agent_name,
    execution_duration_ms,
    invocation_timestamp,
    jsonb_pretty(tool_input_args) as inputs
FROM tool_interactions_view
WHERE execution_duration_ms IS NOT NULL
ORDER BY execution_duration_ms DESC
LIMIT 10;
```

**Get complete tool call lifecycle for analysis:**
```sql
SELECT
    tool_call_id,
    tool_name,
    agent_name,
    pre_reasoning_text,
    jsonb_pretty(tool_input_args) as inputs,
    jsonb_pretty(tool_output_result) as outputs,
    execution_duration_ms,
    llm_decision_timestamp,
    result_timestamp
FROM tool_interactions_view
WHERE tool_call_id = 'tooluse_abc123';
```

**Tool usage statistics:**
```sql
SELECT
    tool_name,
    COUNT(*) as times_used,
    COUNT(DISTINCT agent_name) as agents_using,
    AVG(execution_duration_ms) as avg_duration,
    MIN(execution_duration_ms) as min_duration,
    MAX(execution_duration_ms) as max_duration
FROM tool_interactions_view
GROUP BY tool_name
ORDER BY times_used DESC;
```

### Notes

- **Multi-Tool Handling**: When an LLM response contains multiple tool calls, each tool gets its own row with the same `llm_decision_timestamp` and `post_reasoning_text`
- **Pre-reasoning Coverage**: Not all tool calls have pre-reasoning (depends on agent implementation and conversation state)
- **Pre-reasoning Sources**: Two-pronged extraction strategy (prioritizes `llm_invocation` conversation history, falls back to `agent_progress_update`)
- **Timestamp Consistency**: `pre_reasoning_timestamp` only populated when actual reasoning text exists (prevents NULL text with filled timestamp)
- **Performance**: View uses window functions and joins - consider adding filters for large datasets
- **Analytics Ready**: One row per tool call makes it ideal for BI tools and dashboards
- **Token Tracking**: Token usage captured from the LLM decision phase
- **Lifecycle Complete**: Captures the full "thinking → deciding → executing → receiving" cycle

---

## JSONB Data Structures

### `message_content` (Message Parts Array)

Stores the raw message parts with original structure preserved:

```json
[
  {
    "kind": "data",
    "data": {
      "type": "llm_response",
      "data": {
        "content": {
          "parts": [
            {
              "text": "Response text here..."
            },
            {
              "function_call": {
                "id": "tooluse_IQlYnWTKS8yszLWEYBLqlg",
                "name": "get_decision_trees_hr_decision_trees",
                "args": {
                  "country": "netherlands",
                  "employeeGroup": "Internal Employee",
                  "jobGrade": "CT 12"
                }
              }
            }
          ],
          "role": "model"
        },
        "usage_metadata": {
          "prompt_token_count": 7758,
          "candidates_token_count": 189,
          "total_token_count": 7947
        }
      },
      "usage": {
        "input_tokens": 7758,
        "output_tokens": 189,
        "model": "openai/bedrock-claude-4-5-sonnet-tools"
      }
    }
  }
]
```

### `tool_calls` (Tool Invocations and Results)

Stores tool calls with **original field names preserved** (id, name, args):

```json
[
  {
    "type": "tool_invocation_start",
    "id": "tooluse_IQlYnWTKS8yszLWEYBLqlg",
    "name": "get_decision_trees_hr_decision_trees",
    "args": {
      "country": "netherlands",
      "employeeGroup": "Internal Employee",
      "jobGrade": "CT 12"
    },
    "timestamp": "2025-10-25T23:25:20.219656"
  },
  {
    "type": "tool_result",
    "id": "tooluse_IQlYnWTKS8yszLWEYBLqlg",
    "name": "get_decision_trees_hr_decision_trees",
    "result": {
      "status": "success",
      "data": {...}
    },
    "timestamp": "2025-10-25T23:25:22.543210"
  }
]
```

### `user_context_raw` (Raw User Profile)

Complete user profile with all original field names preserved:

```json
{
  "id": "piyush.krishna@jdecoffee.com",
  "name": "piyush.krishna@jdecoffee.com",
  "email": "Piyush.Krishna@JDEcoffee.com",
  "jobGrade": "CT 12",
  "jobTitle": "Gl Technology Manager eCom & Digital",
  "jobFamily": "E-Commerce (ECM)",
  "country": "Netherlands",
  "location": "Utrecht VV35 NL04 (NL04)",
  "company": "KDE BV (0002)",
  "manager": "05097122",
  "businessUnit": "Finance (00930143)",
  "division": "Global Information Services (00930659)",
  "costCenter": "DTC",
  "contractType": "Indefinite",
  "fte": "1",
  "authenticated": true
}
```

### `token_usage_raw` (Raw Token Data)

Stores token usage as-is from the message, preserving original field names:

```json
{
  "input_tokens": 7758,
  "output_tokens": 189,
  "model": "openai/bedrock-claude-4-5-sonnet-tools"
}
```

or

```json
{
  "prompt_token_count": 7758,
  "candidates_token_count": 189,
  "total_token_count": 7947
}
```

### `metadata` (Message Metadata)

Additional metadata about the message:

```json
{
  "task_status": "working",
  "message_type": true,
  "method": "message/send",
  "message_number": 29
}
```

---

## Query Examples

### Find All Tool Calls

```sql
SELECT
  m.message_id,
  m.context_id,
  tc.value->>'id' as function_call_id,
  tc.value->>'name' as tool_name,
  tc.value->'args' as parameters
FROM messages m,
  jsonb_array_elements(m.tool_calls) AS tc
WHERE m.tool_calls IS NOT NULL
LIMIT 10;
```

### Find Messages with Specific Agent

```sql
SELECT
  message_id,
  timestamp,
  role,
  agent_name,
  metadata->>'task_status' as status
FROM messages
WHERE agent_name = 'JDE_HR_Agent'
ORDER BY timestamp DESC;
```

### Get Conversation with All Messages

```sql
SELECT
  c.context_id,
  c.started_at,
  c.total_messages,
  m.message_id,
  m.timestamp,
  m.role,
  m.agent_name
FROM conversations_derived c
LEFT JOIN messages m ON c.context_id = m.context_id
WHERE c.context_id = 'web-session-16644981729f49ec8d4a6c19ce719f74'
ORDER BY m.timestamp;
```

### Extract Tool Call Arguments

```sql
SELECT
  m.message_id,
  tc.value->>'id' as tool_id,
  tc.value->>'name' as tool_name,
  tc.value->'args'->>'country' as country,
  tc.value->'args'->>'jobGrade' as job_grade
FROM messages m,
  jsonb_array_elements(m.tool_calls) AS tc
WHERE tc.value->>'name' = 'get_decision_trees_hr_decision_trees';
```

### Get Token Usage by Agent

```sql
SELECT
  agent_name,
  COUNT(*) as message_count,
  SUM((token_usage_raw->>'input_tokens')::int) as total_input_tokens,
  SUM((token_usage_raw->>'output_tokens')::int) as total_output_tokens
FROM messages
WHERE token_usage_raw IS NOT NULL
  AND agent_name IS NOT NULL
GROUP BY agent_name;
```

---

## Data Integrity Notes

1. **Single Source of Truth**: All data stored once in messages table
2. **No Normalization**: All JSONB data preserves original field names from upstream sources
3. **Null Handling**: Messages with null `user_properties` are handled gracefully (user_context_raw = null)
4. **Token Storage**: All token usage data stored in `token_usage_raw` JSONB field with variant field names
5. **Tool Call Correlation**: Tool calls can be correlated to results via the `id` field across different messages
6. **Task Hierarchy**: Task/subtask relationships tracked via `task_id` patterns
7. **Derived Views**: Always accurate as they're computed from source table

---

## Schema Evolution

**Version 2.0 (2025-10-30) - Major Simplification**
- **Removed**: `interactions` table (100% redundant - derived from messages)
- **Removed**: `conversations` table (100% redundant - derived from messages)
- **Removed**: `interaction_id` column from messages (was causing stale data)
- **Added**: `conversations_derived` view (always accurate)
- **Added**: `interactions_derived` view (always accurate)
- **Removed**: 37 unused columns total
- **Result**: Single-table design with 2 derived views

**Benefits:**
- No synchronization bugs
- Always accurate data (views computed on-demand)
- Simpler codebase (~250 lines removed)
- No stale aggregations
- Single source of truth
- Can query conversations, interactions, or messages directly

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-10-26 | Initial schema with 3 tables (conversations, interactions, messages) |
| 1.1 | 2025-10-30 | Removed 35 unused/deprecated columns |
| 1.2 | 2025-10-30 | Removed 2 redundant ID columns from messages |
| 1.3 | 2025-10-30 | Bug fix: task_id stores actual task ID |
| **2.0** | **2025-10-30** | **Major refactor: Single-table design with derived views. Dropped interactions and conversations tables.** |
| 2.1 | 2025-10-31 | Removed user_email from conversations_derived, removed num_subtasks and num_tool_calls from interactions_derived |
| 2.2 | 2025-11-01 | Added tool_interactions_view for complete tool call lifecycle tracking with pre-reasoning, LLM decision, invocation, and result phases |
