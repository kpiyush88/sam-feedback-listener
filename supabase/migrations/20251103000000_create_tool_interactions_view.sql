-- Migration: Create tool_interactions_view
-- Description: Simplified view tracking tool call lifecycles with essential fields
-- Date: 2025-11-03

-- Drop view if exists (for idempotency)
DROP VIEW IF EXISTS tool_interactions_view CASCADE;

-- Create the tool_interactions_view
CREATE VIEW tool_interactions_view AS
WITH
-- Extract all messages with parts from raw_payload
all_messages AS (
  SELECT
    raw_payload->>'id' AS source_id,
    COALESCE(
      (raw_payload->'params'->'message'->>'contextId'),
      (raw_payload->'result'->>'contextId')
    ) AS context_id,
    created_at AS message_timestamp,
    jsonb_array_elements(
      COALESCE(
        raw_payload->'params'->'message'->'parts',
        raw_payload->'result'->'status'->'message'->'parts'
      )
    ) AS part,
    topic
  FROM messages
  WHERE (raw_payload->>'id' LIKE 'gdk-task-%' OR raw_payload->>'id' LIKE 'a2a_subtask_%')
    AND (
      (raw_payload->'params'->'message'->'parts') IS NOT NULL
      OR (raw_payload->'result'->'status'->'message'->'parts') IS NOT NULL
    )
),

-- Extract tool calls from llm_invocation messages (function call declarations)
llm_function_calls AS (
  SELECT
    source_id,
    context_id,
    func_call.value->>'id' AS tool_call_id,
    func_call.value->>'name' AS tool_name,
    func_call.value->'args' AS tool_input_args
  FROM (
    SELECT
      source_id,
      context_id,
      jsonb_array_elements((part->'data'->'request'->'contents'))->>'role' AS role,
      jsonb_array_elements(jsonb_array_elements((part->'data'->'request'->'contents')->'parts')) AS model_part
    FROM all_messages
    WHERE (part->'data'->>'type') = 'llm_invocation'
  ) llm_invocations
  CROSS JOIN LATERAL jsonb_array_elements(
    CASE
      WHEN (model_part->'function_call') IS NOT NULL THEN jsonb_build_array(model_part->'function_call')
      ELSE '[]'::jsonb
    END
  ) func_call
  WHERE role = 'model'
),

-- Extract tool invocation start events (when tool execution begins)
tool_invocation_start AS (
  SELECT
    source_id,
    context_id,
    message_timestamp AS invocation_timestamp,
    (part->'data'->>'function_call_id') AS tool_call_id,
    (part->'data'->>'tool_name') AS tool_name,
    (part->'data'->'tool_args') AS tool_input_args
  FROM all_messages
  WHERE (part->'data'->>'type') = 'tool_invocation_start'
),

-- Extract tool results (when tool execution completes)
tool_results AS (
  SELECT
    source_id,
    message_timestamp AS result_timestamp,
    (part->'data'->>'function_call_id') AS tool_call_id,
    (part->'data'->'result_data') AS tool_output_result
  FROM all_messages
  WHERE (part->'data'->>'type') = 'tool_result'
),

-- Combine all tool phases using FULL OUTER JOIN
combined_tool_data AS (
  SELECT
    COALESCE(lfc.source_id, tis.source_id, tr.source_id) AS source_id,
    COALESCE(tis.context_id, lfc.context_id) AS context_id,
    COALESCE(lfc.tool_call_id, tis.tool_call_id, tr.tool_call_id) AS tool_call_id,
    COALESCE(tis.tool_name, lfc.tool_name) AS tool_name,
    COALESCE(tis.tool_input_args, lfc.tool_input_args) AS tool_input_args,
    tis.invocation_timestamp,
    tr.tool_output_result,
    tr.result_timestamp
  FROM llm_function_calls lfc
  FULL JOIN tool_invocation_start tis
    ON lfc.tool_call_id = tis.tool_call_id AND lfc.source_id = tis.source_id
  FULL JOIN tool_results tr
    ON COALESCE(lfc.tool_call_id, tis.tool_call_id) = tr.tool_call_id
    AND COALESCE(lfc.source_id, tis.source_id) = tr.source_id
),

-- Deduplicate by tool_call_id, keeping the record with the most recent invocation_timestamp
deduplicated_tool_data AS (
  SELECT DISTINCT ON (tool_call_id)
    source_id,
    context_id,
    tool_call_id,
    tool_name,
    tool_input_args,
    invocation_timestamp,
    tool_output_result,
    result_timestamp
  FROM combined_tool_data
  WHERE tool_call_id IS NOT NULL
  ORDER BY tool_call_id, invocation_timestamp DESC
)

-- Final SELECT with requested fields
SELECT
  context_id,
  source_id,
  tool_call_id,
  tool_name,
  tool_input_args,
  invocation_timestamp,
  tool_output_result
FROM deduplicated_tool_data
ORDER BY invocation_timestamp DESC;

-- Add comment to the view
COMMENT ON VIEW tool_interactions_view IS
'Simplified analytics view tracking tool call lifecycles with essential fields: context_id, source_id, tool_call_id, tool_name, tool_input_args, invocation_timestamp, and tool_output_result. Deduplicates by tool_call_id keeping the most recent invocation_timestamp.';
