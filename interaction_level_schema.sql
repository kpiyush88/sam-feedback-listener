-- Interaction-Level Schema for SAM Feedback Analysis
-- Groups messages by user input -> agent response pairs instead of just web session

-- Add interaction_id to conversations to track user query/response pairs
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS session_id TEXT;

-- Create interactions table (user input -> response pairs)
CREATE TABLE IF NOT EXISTS interactions (
    interaction_id TEXT PRIMARY KEY NOT NULL,  -- Main task ID (gdk-task-XXX)
    context_id TEXT NOT NULL REFERENCES conversations(context_id),
    interaction_number INTEGER,  -- Sequential number within the conversation
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ,
    duration_seconds NUMERIC,

    -- User input
    user_message_id TEXT,
    user_query TEXT,
    user_query_timestamp TIMESTAMPTZ,

    -- Agent response
    agent_response_message_id TEXT,
    agent_response TEXT,
    agent_response_timestamp TIMESTAMPTZ,
    response_state TEXT,  -- 'completed', 'failed', 'in_progress'

    -- Metrics
    total_messages INTEGER DEFAULT 0,  -- All messages in this interaction (including subtasks)
    total_tokens INTEGER DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cached_tokens INTEGER DEFAULT 0,

    -- Agent orchestration
    primary_agent TEXT,  -- Usually 'OrchestratorAgent'
    delegated_agents TEXT[],  -- Array of agents that were called
    num_subtasks INTEGER DEFAULT 0,
    num_tool_calls INTEGER DEFAULT 0,

    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Update messages table to link to interactions
ALTER TABLE messages ADD COLUMN IF NOT EXISTS interaction_id TEXT REFERENCES interactions(interaction_id);

-- Update tasks table to link to interactions
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS interaction_id TEXT REFERENCES interactions(interaction_id);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_interactions_context_id ON interactions(context_id);
CREATE INDEX IF NOT EXISTS idx_interactions_started_at ON interactions(started_at);
CREATE INDEX IF NOT EXISTS idx_interactions_user_message_id ON interactions(user_message_id);
CREATE INDEX IF NOT EXISTS idx_messages_interaction_id ON messages(interaction_id);
CREATE INDEX IF NOT EXISTS idx_tasks_interaction_id ON tasks(interaction_id);
