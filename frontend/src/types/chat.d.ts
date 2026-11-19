import type { UploadedFile } from './file';

/**
 * Request structure
 */
export interface ChatRequest {
    conversationId: string,
    projectId?: string,
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
    data?: {
        body?: import('./etl').EtlChatBody;
        files?: UploadedFile[];
        [key: string]: unknown;
    } | null;
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


