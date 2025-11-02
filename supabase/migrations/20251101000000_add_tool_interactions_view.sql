-- Migration: Add tool_interactions_view for tracking complete tool call lifecycles
-- Description: Creates a view that aggregates tool calls at interaction level,
--              linking pre-reasoning, LLM decision, tool invocation, and tool result
-- Version: 1.8 - Fix multi-tool call handling + conditional pre_reasoning_timestamp
-- Date: 2025-11-02

-- Drop view if exists (for idempotency)
DROP VIEW IF EXISTS tool_interactions_view;

-- Create the tool_interactions_view
CREATE VIEW tool_interactions_view AS
WITH
-- Step 1: Identify all tool-related messages (phases 2-4 with tool_id)
-- FIXED: Use jsonb_array_elements to handle multiple tool calls per message
tool_phases AS (
    SELECT
        message_id,
        context_id,
        task_id,
        timestamp,
        role,
        agent_name,
        topic,
        message_content,
        tool_calls,
        token_usage_raw,
        -- Extract tool information from EACH tool call in the array
        tc.value->>'id' as tool_call_id,
        tc.value->>'name' as tool_name,
        tc.value->>'type' as tool_phase_type,
        tc.value->'args' as tool_args,
        tc.value->'result' as tool_result
    FROM messages,
         jsonb_array_elements(tool_calls) AS tc
    WHERE tool_calls IS NOT NULL
      AND jsonb_array_length(tool_calls) > 0
      AND tc.value->>'id' IS NOT NULL -- Must have a tool_call_id
),

-- Step 2: Separate each tool phase type into its own CTE
llm_responses AS (
    SELECT
        context_id, task_id, agent_name, tool_call_id, tool_name,
        -- Extract text reasoning from llm_response (first text part if exists)
        (
            SELECT t.elem->>'text'
            FROM jsonb_array_elements(message_content->0->'data'->'data'->'content'->'parts') WITH ORDINALITY AS t(elem, ord)
            WHERE t.elem->>'text' IS NOT NULL
            ORDER BY t.ord
            LIMIT 1
        ) as post_reasoning_text,
        timestamp as llm_decision_timestamp,
        token_usage_raw as token_usage
    FROM tool_phases
    WHERE tool_phase_type = 'llm_response'
),
tool_invocations AS (
    SELECT
        context_id, task_id, agent_name, tool_call_id, tool_name,
        tool_args as tool_input_args,
        timestamp as invocation_timestamp
    FROM tool_phases
    WHERE tool_phase_type = 'tool_invocation_start'
),
tool_results AS (
    SELECT
        context_id, task_id, agent_name, tool_call_id, tool_name,
        tool_result as tool_output_result,
        timestamp as result_timestamp
    FROM tool_phases
    WHERE tool_phase_type = 'tool_result'
),

-- Step 2b: Join all phases together
tool_lifecycle AS (
    SELECT
        COALESCE(lr.context_id, ti.context_id, tr.context_id) as context_id,
        COALESCE(lr.task_id, ti.task_id, tr.task_id) as task_id,
        COALESCE(lr.agent_name, ti.agent_name, tr.agent_name) as agent_name,
        COALESCE(lr.tool_call_id, ti.tool_call_id, tr.tool_call_id) as tool_call_id,
        COALESCE(lr.tool_name, ti.tool_name, tr.tool_name) as tool_name,
        lr.post_reasoning_text,
        lr.llm_decision_timestamp,
        lr.token_usage,
        ti.tool_input_args,
        ti.invocation_timestamp,
        tr.tool_output_result,
        tr.result_timestamp
    FROM llm_responses lr
    FULL OUTER JOIN tool_invocations ti USING (context_id, task_id, agent_name, tool_call_id, tool_name)
    FULL OUTER JOIN tool_results tr USING (context_id, task_id, agent_name, tool_call_id, tool_name)
),

-- Step 3: Find pre-reasoning messages (agent_progress_update) that occur before llm_response
-- IMPORTANT: Apply LEAD across ALL messages, then filter to agent_progress_update
all_messages_with_next AS (
    SELECT
        m.message_id,
        m.context_id,
        m.task_id,
        m.agent_name,
        m.timestamp,
        m.message_content->0->'data'->>'type' as message_type,
        m.message_content->0->'data'->>'status_text' as pre_reasoning_text,
        m.tool_calls,
        -- Find the next message from the same agent in the same task
        LEAD(m.timestamp) OVER (
            PARTITION BY m.task_id, m.agent_name
            ORDER BY m.timestamp
        ) as next_message_timestamp,
        LEAD(m.message_content->0->'data'->>'type') OVER (
            PARTITION BY m.task_id, m.agent_name
            ORDER BY m.timestamp
        ) as next_message_type
    FROM messages m
),
pre_reasoning AS (
    SELECT
        message_id,
        context_id,
        task_id,
        agent_name,
        timestamp,
        pre_reasoning_text,
        next_message_timestamp,
        next_message_type
    FROM all_messages_with_next
    WHERE message_type = 'agent_progress_update'
      AND tool_calls IS NULL  -- Pre-reasoning doesn't have tool_calls
),

-- Step 3b: Extract reasoning from llm_invocation messages (fallback source)
-- These contain conversation history - extract the LAST model message (the reasoning before tool call)
llm_invocation_reasoning AS (
    SELECT
        m.message_id,
        m.context_id,
        m.task_id,
        m.agent_name,
        m.timestamp,
        -- Extract the last model message from conversation history
        (
            SELECT elem->'parts'->0->>'text'
            FROM jsonb_array_elements(m.message_content->0->'data'->'request'->'contents') WITH ORDINALITY AS t(elem, elem_ord)
            WHERE elem->>'role' = 'model'
            ORDER BY elem_ord DESC
            LIMIT 1
        ) as llm_invocation_reasoning_text
    FROM messages m
    WHERE m.message_content->0->'data'->>'type' = 'llm_invocation'
      AND m.message_content->0->'data'->'request'->'contents' IS NOT NULL
),

-- Step 4: Link pre-reasoning to tool lifecycle (two-pronged approach)
-- Primary: Extract from agent_progress_update (user-facing status)
-- Secondary: Extract from llm_invocation (LLM thinking in conversation history)
-- Match by: same task_id, agent_name, and timestamp < llm_decision_timestamp
tool_with_reasoning AS (
    SELECT
        tl.*,
        pr.pre_reasoning_text as agent_progress_reasoning,
        pr.timestamp as agent_progress_timestamp,
        llm_inv.llm_invocation_reasoning_text,
        llm_inv.timestamp as llm_invocation_timestamp
    FROM tool_lifecycle tl
    -- Primary source: agent_progress_update
    LEFT JOIN LATERAL (
        SELECT
            pre_reasoning_text,
            timestamp
        FROM pre_reasoning pr_inner
        WHERE pr_inner.task_id = tl.task_id
          AND pr_inner.agent_name = tl.agent_name
          AND pr_inner.timestamp < COALESCE(tl.llm_decision_timestamp, tl.invocation_timestamp, tl.result_timestamp)
        ORDER BY pr_inner.timestamp DESC
        LIMIT 1
    ) pr ON true
    -- Secondary source: llm_invocation (fallback)
    LEFT JOIN LATERAL (
        SELECT
            llm_invocation_reasoning_text,
            timestamp
        FROM llm_invocation_reasoning llm_inv_inner
        WHERE llm_inv_inner.task_id = tl.task_id
          AND llm_inv_inner.agent_name = tl.agent_name
          AND llm_inv_inner.timestamp < COALESCE(tl.llm_decision_timestamp, tl.invocation_timestamp, tl.result_timestamp)
        ORDER BY llm_inv_inner.timestamp DESC
        LIMIT 1
    ) llm_inv ON true
)

-- Step 5: Final SELECT with calculated fields
SELECT
    -- Extract main task ID for interaction grouping
    CASE
        WHEN task_id LIKE 'gdk-task-%' THEN task_id
        ELSE context_id  -- Fallback to context_id if not a main task
    END as interaction_id,

    context_id,
    task_id,
    tool_call_id,
    tool_name,
    agent_name,

    -- Pre-reasoning phase (two-pronged approach)
    -- Combined reasoning from both sources (llm_invocation has priority for granular data)
    COALESCE(
        llm_invocation_reasoning_text,      -- Primary: LLM thinking (more granular)
        agent_progress_reasoning            -- Secondary: user-facing status (concise)
    ) as pre_reasoning_text,

    -- Source tracking for analytics
    CASE
        WHEN llm_invocation_reasoning_text IS NOT NULL THEN 'llm_invocation'
        WHEN agent_progress_reasoning IS NOT NULL THEN 'agent_progress_update'
        ELSE NULL
    END as pre_reasoning_source,

    -- Timestamps from both sources (only if reasoning text exists)
    CASE
        WHEN llm_invocation_reasoning_text IS NOT NULL THEN llm_invocation_timestamp
        WHEN agent_progress_reasoning IS NOT NULL THEN agent_progress_timestamp
        ELSE NULL
    END as pre_reasoning_timestamp,

    agent_progress_timestamp,
    llm_invocation_timestamp,

    -- Post-reasoning phase (LLM's explanation when deciding to call tool)
    post_reasoning_text,
    llm_decision_timestamp,

    -- Tool invocation phase
    tool_input_args,
    invocation_timestamp,

    -- Tool result phase
    tool_output_result,
    result_timestamp,

    -- Calculated fields
    EXTRACT(EPOCH FROM (result_timestamp - invocation_timestamp)) * 1000 as execution_duration_ms,

    -- Token usage
    token_usage,

    -- Success status (check if result contains success indicator)
    CASE
        WHEN tool_output_result->>'status' = 'success' THEN true
        WHEN tool_output_result->>'status' = 'error' THEN false
        ELSE NULL  -- Unknown
    END as success_status

FROM tool_with_reasoning
ORDER BY invocation_timestamp DESC;

-- Add comment to the view
COMMENT ON VIEW tool_interactions_view IS
'Analytics view tracking complete tool call lifecycles: pre-reasoning (before tool decision), post-reasoning (LLM explanation when calling tool), tool invocation, and tool result. One row per tool call. Handles multiple tool calls per LLM response. Pre-reasoning uses two-pronged extraction (llm_invocation prioritized, then agent_progress_update).';
