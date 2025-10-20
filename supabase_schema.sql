-- Supabase Database Schema for SAM Feedback Analysis

-- Conversations table (top-level grouping by contextId/session)
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    context_id TEXT UNIQUE NOT NULL,
    session_id TEXT,
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
    user_job_grade TEXT,
    metadata JSONB,
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

-- Context retrieval table (tracks what context was retrieved for RAG)
CREATE TABLE IF NOT EXISTS context_retrievals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID REFERENCES messages(id) ON DELETE CASCADE,
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    query TEXT,
    retrieved_documents JSONB,
    num_documents INTEGER,
    retrieval_method TEXT,
    filters_applied JSONB,
    timestamp TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Evaluations table (stores evaluation results)
CREATE TABLE IF NOT EXISTS evaluations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    message_id UUID REFERENCES messages(id) ON DELETE CASCADE,
    evaluation_type TEXT NOT NULL, -- 'groundedness', 'context_relevance', 'answer_relevance', 'verbosity', 'toxicity', etc.
    score NUMERIC,
    passed BOOLEAN,
    details JSONB,
    evaluated_at TIMESTAMPTZ DEFAULT NOW(),
    evaluator TEXT, -- 'llm', 'rule-based', 'human'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Performance metrics table
CREATE TABLE IF NOT EXISTS performance_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID REFERENCES tasks(id) ON DELETE CASCADE,
    metric_type TEXT NOT NULL, -- 'latency', 'token_usage', 'cost', etc.
    metric_value NUMERIC,
    unit TEXT, -- 'seconds', 'tokens', 'usd', etc.
    timestamp TIMESTAMPTZ NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Human feedback table
CREATE TABLE IF NOT EXISTS human_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    message_id UUID REFERENCES messages(id) ON DELETE CASCADE,
    feedback_type TEXT, -- 'thumbs_up', 'thumbs_down', 'rating', 'comment'
    rating INTEGER, -- 1-5 scale
    comment TEXT,
    user_id TEXT,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Topic clusters table (for conversation topic modeling)
CREATE TABLE IF NOT EXISTS topic_clusters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
    cluster_id TEXT,
    topic_label TEXT,
    keywords TEXT[],
    confidence NUMERIC,
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
CREATE INDEX IF NOT EXISTS idx_evaluations_conversation_id ON evaluations(conversation_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_evaluation_type ON evaluations(evaluation_type);
CREATE INDEX IF NOT EXISTS idx_human_feedback_conversation_id ON human_feedback(conversation_id);

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
