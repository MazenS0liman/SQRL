import React, { useCallback, useEffect, useState } from 'react';
import { Chat } from '@/components/chat/Chat';
import { Message } from '@/components/chat/Message';
import type { ChatMessage, ChatSessionData, UploadedFile } from '@/types';
import { authHeaders } from '@/lib/auth';

interface ChatPageProps {
	session: ChatSessionData | null;
}

export const ChatPage: React.FC<ChatPageProps> = ({ session }: ChatPageProps) => {
	const [activeSession, setActiveSession] = useState<ChatSessionData | null>(session);
	const hasMessages = (activeSession?.messages?.length ?? 0) > 0;

	useEffect(() => {
		setActiveSession(session);
	}, [session]);

	const buildSessionTitle = (message: string) => {
		const trimmed = message.trim();
		if (!trimmed) return 'New chat';
		return trimmed.length > 48 ? `${trimmed.slice(0, 48)}...` : trimmed;
	};

	const getMessageSummary = (message: ChatMessage) => {
		const text = message.content.trim();
		if (text) return text;
		const files = (message.data?.files as UploadedFile[] | undefined) ?? [];
		if (files.length === 1) return files[0].fileName;
		if (files.length > 1) return `${files.length} files attached`;
		return 'New chat';
	};

	const createSessionFromMessage = (message: ChatMessage): ChatSessionData => ({
		id: `session-${Date.now()}`,
		title: buildSessionTitle(getMessageSummary(message)),
		lastMessage: getMessageSummary(message),
		timestamp: message.timestamp,
		messages: [message],
	});

	const updateSession = useCallback((message: ChatMessage) => {
		setActiveSession((currentSession) => {
			if (!currentSession) {
				return createSessionFromMessage(message);
			}

			const messages = [...currentSession.messages, message];
			return {
				...currentSession,
				messages,
				lastMessage: getMessageSummary(message),
				timestamp: message.timestamp,
			};
		});
	}, []);

	const handleUserMessage = useCallback(async (message: ChatMessage) => {
		updateSession(message);

		const baseUrl = (import.meta.env.VITE_BACKEND_API_BASE_URL || "/api").replace(/\/$/, '');
		const fileUrls = (message.data?.files as UploadedFile[] | undefined)?.map((f) => f.fileUrl) ?? [];

		// conversationId must match ^[a-zA-Z0-9_-]+$
		const rawId = activeSession?.id ?? `session-${Date.now()}`;
		const conversationId = rawId.replace(/[^a-zA-Z0-9_-]/g, '-');

		// Build conversation history from existing messages (max 10, excluding the one just sent)
		const conversationHistory = (activeSession?.messages ?? [])
			.slice(-10)
			.map((m) => ({
				role: m.role,
				content: m.content,
				timestamp: m.timestamp.toISOString(),
			}));

		try {
			const response = await fetch(`${baseUrl}/chat`, {
				method: 'POST',
				headers: authHeaders({ 'Content-Type': 'application/json' }),
				body: JSON.stringify({
					query: message.content,
					conversationId,
					fileUrls,
					conversationHistory,
				}),
			});

			if (!response.ok) throw new Error(`Chat request failed: ${response.statusText}`);

			const result = await response.json();

			updateSession({
				id: `assistant-${Date.now()}`,
				role: 'assistant',
				content: result.reply,
				timestamp: new Date(result.timestamp),
				type: 'text',
				data: result.body ? { body: result.body } : null,
			});
		} catch (err) {
			console.error('Chat error:', err);

			updateSession({
				id: `assistant-error-${Date.now()}`,
				role: 'assistant',
				content: 'Something went wrong. Please try again.',
				timestamp: new Date(),
				type: 'text',
			});
		}
	}, [updateSession, activeSession]);

  return (
    <div style={{
      height: '100vh',
      display: 'flex',
      flexDirection: 'column',
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    }}>
      <div style={{
			width: '100%',
			height: '100%',
			display: 'flex',
			flexDirection: 'column',
			overflow: 'hidden',
			padding: '24px 20px 20px',
		}}>
			<div style={{
				flex: 1,
				display: 'flex',
				flexDirection: 'column',
				overflow: 'hidden',
			}}>
				{hasMessages ? (
					<div style={{
						flex: 1,
						overflowY: 'auto',
						display: 'flex',
						flexDirection: 'column',
						gap: '16px',
						paddingRight: '8px',
						paddingBottom: '20px',
						maxWidth: '960px',
						width: '100%',
						margin: '0 auto',
					}}>
						{activeSession?.messages.map((message) => (
							<Message key={message.id} message={message} onUpdateData={() => {}} />
						))}
					</div>
				) : (
					<div style={{
						flex: 1,
						display: 'flex',
						alignItems: 'center',
						justifyContent: 'center',
						textAlign: 'center',
						color: 'hsl(249, 7%, 61%)',
						fontSize: '1.9rem',
						fontWeight: 600,
						letterSpacing: '-0.02em',
					}}>
						What can I do for you today?
					</div>
				)}
			</div>

			<div style={{
				marginTop: 'auto',
				paddingTop: '16px',
				maxWidth: '960px',
				width: '100%',
				marginLeft: 'auto',
				marginRight: 'auto',
			}}>
				<Chat
					key={activeSession ? activeSession.id : 'new-session'}
					handleAddMessage={handleUserMessage}
				/>
			</div>
		</div>
    </div>
  );
};