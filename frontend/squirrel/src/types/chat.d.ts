/**
 * Request structure
 */
export interface ChatRequest {
    conversationId: string,
    query: string
}

/**
 * Message content types
 */
export type MessageContentType = 'text' | 'diagram' | 'pdf' | 'md' | 'csv' | 'file'

/**
 * Interface for chat messages
 */
export interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    timestamp: Date;
    type?: MessageContentType;
    data?: any;
}

/**
 * Interface for chat session summary
 */
export interface ChatSession {
    id: string;
    title: string;
    lastMessage: string;
    timestamp: Date;
}

/**
 * Interface for chat session data
 */
export interface ChatSessionData extends ChatSession {
    messages: ChatMessage[]
}


