-- Migration: Add atomic increment function for conversation stats
-- This function prevents race conditions when updating conversation statistics

CREATE OR REPLACE FUNCTION increment_conversation_stats(
    p_context_id TEXT,
    p_message_increment INTEGER DEFAULT 1,
    p_token_increment INTEGER DEFAULT 0,
    p_input_token_increment INTEGER DEFAULT 0,
    p_output_token_increment INTEGER DEFAULT 0,
    p_cached_token_increment INTEGER DEFAULT 0,
    p_ended_at TIMESTAMPTZ DEFAULT NOW()
)
RETURNS VOID AS $$
BEGIN
    UPDATE conversations
    SET
        total_messages = COALESCE(total_messages, 0) + p_message_increment,
        total_tokens = COALESCE(total_tokens, 0) + p_token_increment,
        total_input_tokens = COALESCE(total_input_tokens, 0) + p_input_token_increment,
        total_output_tokens = COALESCE(total_output_tokens, 0) + p_output_token_increment,
        total_cached_tokens = COALESCE(total_cached_tokens, 0) + p_cached_token_increment,
        ended_at = p_ended_at,
        updated_at = NOW()
    WHERE context_id = p_context_id;
END;
$$ LANGUAGE plpgsql;

-- Add comment
COMMENT ON FUNCTION increment_conversation_stats IS 'Atomically increments conversation statistics to prevent race conditions during parallel message processing';
