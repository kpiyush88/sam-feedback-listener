-- Migration: Create interactions_view
-- Description: View tracking user interactions with first message and final response
-- Date: 2025-11-02

-- Drop view if exists (for idempotency)
DROP VIEW IF EXISTS interactions_view CASCADE;

-- Create the interactions_view
CREATE VIEW interactions_view AS
WITH
-- Extract first messages (user queries)
first_messages AS (
  SELECT DISTINCT ON (raw_payload->>'id')
    raw_payload->>'id' AS interaction_id,
    created_at,
    raw_payload->'params'->'message'->>'contextId' AS context_id,
    raw_payload->'params'->'message'->'metadata'->>'agent_name' AS agent_name,
    raw_payload->'params'->'message'->'parts'->1->>'text' AS user_query,
    user_context_raw AS user_profile
  FROM messages
  WHERE raw_payload->>'id' LIKE 'gdk-task-%'
    AND raw_payload->'params'->'message'->>'role' = 'user'
    AND raw_payload->'params'->'message'->'parts' IS NOT NULL
  ORDER BY raw_payload->>'id', created_at
),

-- Extract final responses (agent responses)
final_responses AS (
  SELECT DISTINCT ON (raw_payload->>'id')
    raw_payload->>'id' AS interaction_id,
    raw_payload->'result'->'status'->'message'->'parts'->0->>'text' AS final_response,
    created_at AS response_timestamp
  FROM messages
  WHERE raw_payload->>'id' LIKE 'gdk-task-%'
    AND topic LIKE '%gateway/response%'
    AND raw_payload->'result'->'status'->'message'->'parts'->0->>'text' IS NOT NULL
  ORDER BY raw_payload->>'id', created_at DESC
),

-- Extract peer agent delegations from tool_interactions_view
-- This identifies which agent was delegated to handle the interaction
peer_delegations AS (
  SELECT
    tiv.interaction_id,
    tiv.tool_output_result->'metadata'->>'agent_name' as delegated_agent,
    ROW_NUMBER() OVER (PARTITION BY tiv.interaction_id ORDER BY tiv.invocation_timestamp) as rn
  FROM tool_interactions_view tiv
  WHERE tiv.tool_name LIKE 'peer_%'
    AND tiv.tool_output_result->'metadata'->>'agent_name' IS NOT NULL
)

-- Join first messages with final responses and calculate duration
SELECT
  fm.interaction_id,
  fm.context_id,
  fm.created_at,
  fm.agent_name,
  -- Determine the handling agent based on the logic:
  -- 1. If not orchestrator, use the agent_name (direct handling)
  -- 2. If orchestrator with peer delegation, use the delegated agent
  -- 3. If orchestrator without delegation, use OrchestratorAgent
  CASE
    WHEN fm.agent_name != 'OrchestratorAgent' THEN fm.agent_name
    WHEN pd.delegated_agent IS NOT NULL THEN pd.delegated_agent
    ELSE fm.agent_name
  END as handling_agent,
  fm.user_query,
  fm.user_profile,
  fr.final_response,
  -- Calculate duration: response_timestamp - created_at
  -- Returns duration in seconds as a numeric value (e.g., 2.5 for 2.5 seconds)
  CASE
    WHEN fr.response_timestamp IS NOT NULL AND fm.created_at IS NOT NULL
    THEN EXTRACT(EPOCH FROM (fr.response_timestamp - fm.created_at))
    ELSE NULL
  END AS duration
FROM first_messages fm
LEFT JOIN final_responses fr ON fm.interaction_id = fr.interaction_id
LEFT JOIN peer_delegations pd ON fm.interaction_id = pd.interaction_id AND pd.rn = 1
ORDER BY fm.created_at DESC;

-- Add comment to the view
COMMENT ON VIEW interactions_view IS
'Analytics view tracking user interactions from initial query to final response. Fields: interaction_id (gdk-task ID), context_id, created_at (timestamp of user query), agent_name (initially invoked agent), handling_agent (agent that actually handled the interaction), user_query, user_profile (user context), final_response (agent''s final answer), and duration (numeric seconds from query to response). The handling_agent differs from agent_name when OrchestratorAgent delegates to peer agents. Each row represents one complete user interaction.';
