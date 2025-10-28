# SAM Feedback Listener - Database Schema Documentation

**Version**: 1.0
**Last Updated**: 2025-10-26
**Database**: Supabase PostgreSQL

---

## Overview

The database consists of three main tables that track agent conversations, interactions, and individual messages with complete JSONB storage of raw message data.

### Table Relationships

```
conversations (1)
    ↓
interactions (N)
    ↓
messages (N)
```

---

## Table: `conversations`

Represents a single conversation session with a user, containing aggregated statistics.

### Columns

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `context_id` | text | NO | - | **Primary Key**. Unique identifier for the conversation session (e.g., `web-session-16644981729f49ec8d4a6c19ce719f74`) |
| `started_at` | timestamp with time zone | NO | - | When the conversation started |
| `ended_at` | timestamp with time zone | YES | - | When the conversation ended |
| `total_messages` | integer | YES | 0 | Count of all messages in conversation |
| `total_tokens` | integer | YES | 0 | Aggregated total tokens across all messages |
| `total_input_tokens` | integer | YES | 0 | Aggregated input tokens |
| `total_output_tokens` | integer | YES | 0 | Aggregated output tokens |
| `total_cached_tokens` | integer | YES | 0 | Aggregated cached tokens |
| `user_id` | text | YES | - | User identifier |
| `user_email` | text | YES | - | User email address |
| `user_name` | text | YES | - | User display name |
| `user_country` | text | YES | - | User's country |
| `user_company` | text | YES | - | User's company |
| `user_location` | text | YES | - | User's location |
| `user_language` | text | YES | - | User's preferred language |
| `user_authenticated` | boolean | YES | - | Whether user is authenticated |
| `session_id` | text | YES | - | Session identifier |
| `metadata` | jsonb | YES | - | Additional conversation metadata |
| `user_context_raw` | jsonb | YES | - | **Raw complete user profile** with original field names (jobGrade, workEmail, etc.) |
| `created_at` | timestamp with time zone | YES | now() | Record creation timestamp |
| `updated_at` | timestamp with time zone | YES | now() | Record last update timestamp |

### Indexes

- `context_id` (PRIMARY KEY)

### Example `user_context_raw` JSONB Structure

```json
{
  "id": "piyush.krishna@jdecoffee.com",
  "name": "piyush.krishna@jdecoffee.com",
  "email": "Piyush.Krishna@JDEcoffee.com",
  "jobGrade": "CT 12",
  "jobTitle": "Gl Technology Manager eCom & Digital",
  "jobFamily": "E-Commerce (ECM)",
  "jobSubFamily": "E-Commerce",
  "department": "e-Com Technology (89932173)",
  "country": "Netherlands",
  "location": "Utrecht VV35 NL04 (NL04)",
  "company": "KDE BV (0002)",
  "manager": "05097122",
  "managerName": "Leonie Ham",
  "businessUnit": "Finance (00930143)",
  "division": "Global Information Services (00930659)",
  "costCenter": "DTC",
  "contractType": "Indefinite",
  "employeeGroup": "Internal Employee",
  "fte": "1",
  "positionGrade": "CT 12",
  "salaryStructure": "NLD_LOC_C&T",
  "securityCode": null,
  "nativePreferredLanguage": "English",
  "authenticated": true,
  "auth_method": "oidc"
}
```

---

## Table: `interactions`

Represents a single user-agent interaction cycle (user query → agent response), tracking the conversation flow.

### Columns

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `interaction_id` | text | NO | - | **Primary Key**. Main task ID (e.g., `gdk-task-a396adc8b73640d486e06ee59474c199`) |
| `context_id` | text | NO | - | **Foreign Key** → `conversations.context_id` |
| `interaction_number` | integer | YES | - | Sequential number of this interaction in the conversation |
| `started_at` | timestamp with time zone | NO | - | When the interaction started |
| `completed_at` | timestamp with time zone | YES | - | When the interaction completed |
| `duration_seconds` | numeric | YES | - | Total duration in seconds |
| `user_message_id` | text | YES | - | Message ID of the user's query |
| `user_query` | text | YES | - | The user's original query text |
| `user_query_timestamp` | timestamp with time zone | YES | - | When user query was received |
| `agent_response_message_id` | text | YES | - | Message ID of the agent's response |
| `agent_response` | text | YES | - | The agent's final response text |
| `agent_response_timestamp` | timestamp with time zone | YES | - | When agent responded |
| `response_state` | text | YES | - | State of response: `in_progress`, `completed`, `failed` |
| `primary_agent` | text | YES | - | Name of primary agent handling the interaction |
| `delegated_agents` | text[] | YES | - | Array of agent names that were delegated subtasks |
| `total_messages` | integer | YES | 0 | Count of messages in this interaction |
| `num_subtasks` | integer | YES | 0 | Number of delegated subtasks |
| `num_tool_calls` | integer | YES | 0 | Total tool calls made during interaction |
| `total_tokens` | integer | YES | 0 | Total tokens used |
| `total_input_tokens` | integer | YES | 0 | Input tokens |
| `total_output_tokens` | integer | YES | 0 | Output tokens |
| `total_cached_tokens` | integer | YES | 0 | Cached tokens |
| `metadata` | jsonb | YES | - | Additional interaction metadata |
| `created_at` | timestamp with time zone | YES | now() | Record creation timestamp |
| `updated_at` | timestamp with time zone | YES | now() | Record last update timestamp |

### Indexes

- `interaction_id` (PRIMARY KEY)
- `context_id` (FOREIGN KEY)

---

## Table: `messages`

Individual messages in the conversation, storing complete message content and metadata with JSONB for flexible storage.

### Columns

#### Indexed/Core Fields

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `message_id` | text | NO | - | **Primary Key**. Unique message identifier |
| `context_id` | text | YES | - | **Foreign Key** → `conversations.context_id` |
| `task_id` | text | YES | - | Associated task ID (main task identifier) |
| `interaction_id` | text | YES | - | **Foreign Key** → `interactions.interaction_id` |
| `timestamp` | timestamp with time zone | NO | - | When the message was created |
| `role` | text | YES | - | Message role: `user`, `agent`, `system` |
| `agent_name` | text | YES | - | Name of the agent if role is `agent` |
| `topic` | text | YES | - | Message topic path (e.g., `jde-sam-test/a2a/v1/agent/status/OrchestratorAgent/...`) |
| `feedback_id` | text | YES | - | Feedback identifier |
| `correlation_id` | text | YES | - | Correlation ID extracted from topic for joining |
| `sender_id` | text | YES | - | ID of the message sender |
| `message_kind` | text | YES | - | Kind of message (from original payload) |
| `is_final` | boolean | YES | false | Whether this is a final message |

#### Token Usage Fields

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `input_tokens` | integer | YES | - | Input tokens used by this message |
| `output_tokens` | integer | YES | - | Output tokens generated by this message |
| `total_tokens` | integer | YES | - | Total tokens for this message |
| `model_used` | text | YES | - | Model name that processed the message |

#### JSONB Columns (Flexible Data Storage)

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| `message_content` | jsonb | YES | **Raw message parts array** - The message payload structure as-is from original |
| `tool_calls` | jsonb | YES | **Tool invocations and results** - Array of tool calls with original field names preserved |
| `raw_payload` | jsonb | YES | **Complete original payload** - Full raw message payload from upstream |
| `user_context_raw` | jsonb | YES | **Raw user profile** - Complete user profile with original field names |
| `token_usage_raw` | jsonb | YES | **Raw token usage data** - Token usage data as-is from original (may have variant field names) |
| `user_context` | jsonb | YES | Processed user context (legacy, may be deprecated) |
| `message_payload` | jsonb | YES | Additional message payload (legacy) |
| `request_data` | jsonb | YES | Request data if applicable |
| `status_data` | jsonb | YES | Status-related data |
| `metadata` | jsonb | YES | Additional metadata (task_status, message_type, method, message_number, etc.) |

#### System Fields

| Column | Type | Nullable | Default | Description |
|--------|------|----------|---------|-------------|
| `created_at` | timestamp with time zone | YES | now() | Record creation timestamp |
| `updated_at` | timestamp with time zone | YES | now() | Record last update timestamp |

### Indexes

- `message_id` (PRIMARY KEY)
- `context_id` (FOREIGN KEY)
- `interaction_id` (FOREIGN KEY)

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
      "jobGrade": "CT 12",
      "positionGrade": "CT 12",
      "companyCode": "0002"
    },
    "timestamp": "2025-10-25T23:25:20.219656"
  },
  {
    "type": "llm_response",
    "id": "tooluse_eCu6UdBFQw-l-AZqBaqbKg",
    "name": "create_chart_from_plotly_config",
    "args": {
      "config_format": "json",
      "output_format": "png",
      "config_content": "{...}",
      "output_filename": "garden_growth_chart.png"
    },
    "timestamp": "2025-10-25T23:25:20.219656"
  }
]
```

**Key Point**: Original field names are preserved:
- `id` (not `function_call_id`)
- `name` (not `tool_name`)
- `args` (not `parameters`)

### `raw_payload` (Complete Original Payload)

Stores the entire original message payload structure:

```json
{
  "id": "gdk-task-a396adc8b73640d486e06ee59474c199",
  "jsonrpc": "2.0",
  "result": {
    "contextId": "web-session-16644981729f49ec8d4a6c19ce719f74",
    "final": false,
    "kind": "status-update",
    "status": {
      "message": {
        "contextId": "web-session-16644981729f49ec8d4a6c19ce719f74",
        "kind": "message",
        "messageId": "...",
        "parts": [...],
        "role": "agent",
        "taskId": "gdk-task-a396adc8b73640d486e06ee59474c199"
      },
      "state": "working",
      "timestamp": "2025-10-24T12:48:51.740104+00:00"
    },
    "taskId": "gdk-task-a396adc8b73640d486e06ee59474c199"
  }
}
```

### `user_context_raw` (Raw User Profile)

Same as in conversations table - complete user profile with all original field names preserved.

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

### Find All Tool Calls with Correlation

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

### Get Conversation with All Interactions and Messages

```sql
SELECT
  c.context_id,
  c.started_at,
  i.interaction_id,
  i.user_query,
  i.agent_response,
  COUNT(m.message_id) as message_count
FROM conversations c
LEFT JOIN interactions i ON c.context_id = i.context_id
LEFT JOIN messages m ON i.interaction_id = m.interaction_id
WHERE c.context_id = 'web-session-16644981729f49ec8d4a6c19ce719f74'
GROUP BY c.context_id, c.started_at, i.interaction_id, i.user_query, i.agent_response
ORDER BY i.started_at;
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

### Find User Profile Information

```sql
SELECT
  m.message_id,
  m.user_context_raw->>'id' as user_id,
  m.user_context_raw->>'name' as user_name,
  m.user_context_raw->>'email' as user_email,
  m.user_context_raw->>'jobGrade' as job_grade,
  m.user_context_raw->>'country' as country
FROM messages m
WHERE m.user_context_raw IS NOT NULL
LIMIT 10;
```

---

## Data Integrity Notes

1. **No Normalization**: All JSONB data preserves original field names from upstream sources
2. **Null Handling**: Messages with null `user_properties` are handled gracefully (user_context_raw = null)
3. **Token Variants**: Token usage may have different field names depending on source:
   - `input_tokens`, `output_tokens` (from usage object)
   - `prompt_token_count`, `candidates_token_count` (from usage_metadata)
4. **Tool Call Correlation**: Tool calls can be correlated to results via the `id` field across different messages
5. **Cascading**: Interactions and messages cascade from conversations via context_id

---

## Current Statistics

- **Total Conversations**: 2
- **Total Interactions**: 3
- **Total Messages**: 248
- **Messages with Tool Calls**: 26
- **Unique Tool Invocations**: 9
- **Tool Types**: generate_answer_with_citations, get_decision_trees_hr_decision_trees, peer_JDE_HR_Agent, list_artifacts, signal_artifact_for_return, create_chart_from_plotly_config

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2025-10-26 | Initial schema documentation with JSONB field details |
