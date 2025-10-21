-- Supabase Database Schema for SAM Feedback Analysis

-- Conversations table (top-level grouping by contextId/session)
CREATE TABLE IF NOT EXISTS conversations (
    context_id TEXT PRIMARY KEY NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    total_messages INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cached_tokens INTEGER DEFAULT 0,
    user_email TEXT,
    user_name TEXT,
    user_country TEXT,
    user_id TEXT,
    user_company TEXT,
    user_location TEXT,
    user_language TEXT,
    user_authenticated BOOLEAN,
    metadata JSONB,  -- Contains user_profile with job_grade, job_title, department, etc.
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tasks table (main tasks and subtasks)
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    task_id TEXT UNIQUE NOT NULL,
    parent_task_id TEXT,
    agent_name TEXT,
    task_type TEXT, -- 'main' or 'subtask'
    status TEXT, -- 'working', 'completed', 'failed'
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    duration_seconds NUMERIC,
    total_tokens INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    model_used TEXT,
    artifacts_produced JSONB,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Messages table (individual messages in the conversation)
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    message_id TEXT NOT NULL,
    role TEXT NOT NULL, -- 'user', 'agent', 'system'
    message_type TEXT, -- 'message', 'status-update', 'tool-call', etc.
    agent_name TEXT,
    content TEXT,
    parts JSONB,
    timestamp TIMESTAMPTZ NOT NULL,
    topic TEXT,
    correlation_id TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Tool calls table (tracks tool/function calls by agents)
CREATE TABLE IF NOT EXISTS tool_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID REFERENCES messages(id) ON DELETE CASCADE,
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    function_call_id TEXT,
    parameters JSONB,
    result JSONB,
    status TEXT, -- 'called', 'success', 'error'
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_conversations_context_id ON conversations(context_id);
CREATE INDEX IF NOT EXISTS idx_conversations_started_at ON conversations(started_at);
CREATE INDEX IF NOT EXISTS idx_tasks_conversation_id ON tasks(conversation_id);
CREATE INDEX IF NOT EXISTS idx_tasks_task_id ON tasks(task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent_task_id ON tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_task_id ON messages(task_id);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_tool_calls_task_id ON tool_calls(task_id);

-- JSONB/JSON indexes for metadata columns using GIN (Generalized Inverted Index)
-- These allow fast queries on JSONB fields using operators like @>, ?, ?&, ?|
CREATE INDEX IF NOT EXISTS idx_conversations_metadata_gin ON conversations USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_tasks_metadata_gin ON tasks USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_messages_metadata_gin ON messages USING GIN (metadata);
CREATE INDEX IF NOT EXISTS idx_tool_calls_parameters_gin ON tool_calls USING GIN (parameters);
CREATE INDEX IF NOT EXISTS idx_tool_calls_result_gin ON tool_calls USING GIN (result);

-- Create updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create triggers for updated_at
CREATE TRIGGER update_conversations_updated_at BEFORE UPDATE ON conversations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_tasks_updated_at BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
