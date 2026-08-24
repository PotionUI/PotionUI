import type { AxiosInstance } from 'axios';
import type {
	APIResponse,
	ChatMessageResponse,
	ChatSessionResponse,
	ChatSessionWithMessagesResponse,
	SendChatMessageResponse
} from '$lib/types/api';
import type { PreChatAction } from '$lib/types/llm';
import type { ChatMode, ChatToolInfo, MemoryNote, ResourceSuggestion } from '$lib/types/chat';

/**
 * Read a fetch Response body as an SSE stream, invoking `onEvent` per event.
 * Shared by the POST send stream and the GET reattach stream so both parse the
 * `event:`/`data:` framing identically.
 */
async function readSseStream(
	response: Response,
	onEvent?: (event: { type: string; data: any }) => void
): Promise<void> {
	const reader = response.body?.getReader();
	if (!reader) throw new Error('No response body');

	const decoder = new TextDecoder();
	let buffer = '';
	let currentEventType = 'message';

	try {
		while (true) {
			const { done, value } = await reader.read();
			if (done) break;

			buffer += decoder.decode(value, { stream: true });
			const lines = buffer.split('\n');
			buffer = lines.pop() || '';

			for (const line of lines) {
				if (line.startsWith('event: ')) {
					currentEventType = line.slice(7).trim();
				} else if (line.startsWith('data: ')) {
					try {
						const data = JSON.parse(line.slice(6));
						onEvent?.({ type: currentEventType, data });
					} catch {
						// Skip malformed JSON
					}
					currentEventType = 'message';
				}
			}
		}
	} finally {
		reader.releaseLock();
	}
}

export function createChatApi(client: AxiosInstance, getToken: () => string | null, getBaseURL: () => string, onAuthExpired?: () => void) {
	return {
		async createChatSession(request: {
			original_text?: string;
			llm_config_id?: string;
			mode?: string;
			name?: string;
			system_message?: string;
			enabled_tools?: string[];
		}): Promise<APIResponse<ChatSessionResponse>> {
			const response = await client.post('/api/chat/sessions', request);
			return response.data;
		},

		async getChatModes(): Promise<APIResponse<{ modes: ChatMode[] }>> {
			const response = await client.get('/api/chat/modes');
			return response.data;
		},

		async getChatSessions(
			options: { mode?: string; search?: string; limit?: number; offset?: number } = {}
		): Promise<
			APIResponse<{
				sessions: ChatSessionResponse[];
				total: number;
				limit: number;
				offset: number;
			}>
		> {
			const params = new URLSearchParams();
			if (options.mode) params.append('mode', options.mode);
			if (options.search) params.append('search', options.search);
			params.append('limit', (options.limit ?? 20).toString());
			if (options.offset) params.append('offset', options.offset.toString());
			const response = await client.get(`/api/chat/sessions?${params.toString()}`);
			return response.data;
		},

		async getChatSession(
			sessionId: string
		): Promise<APIResponse<ChatSessionWithMessagesResponse>> {
			const response = await client.get(`/api/chat/sessions/${sessionId}`);
			return response.data;
		},

		async sendChatMessage(
			sessionId: string,
			options: {
				content: string;
				imageData?: string;
				timeoutSeconds?: number;
				contextMetadata?: Record<string, any>;
				resources?: Array<{ uri: string }>;
			}
		): Promise<APIResponse<SendChatMessageResponse>> {
			const timeout = options.timeoutSeconds ? options.timeoutSeconds * 1000 : 120000;
			const response = await client.post(
				`/api/chat/sessions/${sessionId}/messages`,
				{
					content: options.content,
					image_data: options.imageData || undefined,
					context_metadata: options.contextMetadata || undefined,
					resources: options.resources?.length ? options.resources : undefined
				},
				{ timeout }
			);
			return response.data;
		},

		async sendChatMessageStream(
			sessionId: string,
			options: {
				content: string;
				imageData?: string;
				contextMetadata?: Record<string, any>;
				resources?: Array<{ uri: string }>;
			},
			onEvent?: (event: { type: string; data: any }) => void
		): Promise<void> {
			const baseURL = getBaseURL();
			const token = getToken();
			const headers: Record<string, string> = {
				'Content-Type': 'application/json'
			};
			if (token) {
				headers['Authorization'] = `Bearer ${token}`;
			}

			const response = await fetch(
				`${baseURL}/api/chat/sessions/${sessionId}/messages/stream`,
				{
					method: 'POST',
					headers,
					body: JSON.stringify({
						content: options.content,
						image_data: options.imageData || undefined,
						context_metadata: options.contextMetadata || undefined,
						resources: options.resources?.length ? options.resources : undefined
					})
				}
			);

			if (response.status === 401) {
				onAuthExpired?.();
				throw new Error('Authentication expired');
			}

			if (!response.ok) {
				throw new Error(`HTTP ${response.status}: ${response.statusText}`);
			}

			await readSseStream(response, onEvent);
		},

		/**
		 * Reattach to a turn already running on the backend for this session
		 * (e.g. after a page reload). Replays the turn from its start and then
		 * streams live, feeding the same event shape as sendChatMessageStream.
		 */
		async reattachChatMessageStream(
			sessionId: string,
			onEvent?: (event: { type: string; data: any }) => void
		): Promise<void> {
			const baseURL = getBaseURL();
			const token = getToken();
			const headers: Record<string, string> = {};
			if (token) {
				headers['Authorization'] = `Bearer ${token}`;
			}

			const response = await fetch(
				`${baseURL}/api/chat/sessions/${sessionId}/messages/stream`,
				{ method: 'GET', headers }
			);

			if (response.status === 401) {
				onAuthExpired?.();
				throw new Error('Authentication expired');
			}

			if (!response.ok) {
				throw new Error(`HTTP ${response.status}: ${response.statusText}`);
			}

			await readSseStream(response, onEvent);
		},

		/** Explicitly stop the session's in-flight turn (the stop button). */
		async cancelChatTurn(sessionId: string): Promise<APIResponse<{ cancelled: boolean }>> {
			const response = await client.post(`/api/chat/sessions/${sessionId}/turn/cancel`);
			return response.data;
		},

		async deleteChatSession(sessionId: string): Promise<APIResponse<void>> {
			const response = await client.delete(`/api/chat/sessions/${sessionId}`);
			return response.data;
		},

		async suggestChatResources(
			query: string,
			mode?: string,
			limit: number = 15
		): Promise<APIResponse<{ suggestions: ResourceSuggestion[] }>> {
			const params = new URLSearchParams();
			params.append('query', query);
			if (mode) params.append('mode', mode);
			params.append('limit', limit.toString());
			const response = await client.get(`/api/chat/resources/suggest?${params.toString()}`);
			return response.data;
		},

		async listChatTools(mode?: string): Promise<APIResponse<{ tools: ChatToolInfo[] }>> {
			const query = mode ? `?mode=${encodeURIComponent(mode)}` : '';
			const response = await client.get(`/api/chat/tools${query}`);
			return response.data;
		},

		async approveToolExecution(
			sessionId: string,
			data: { message_id: string; tool_index: number; approved: boolean }
		): Promise<any> {
			const response = await client.post(
				`/api/chat/sessions/${sessionId}/tools/approve`,
				data
			);
			return response.data;
		},

		async getPreChatActions(): Promise<APIResponse<{ actions: PreChatAction[] }>> {
			const response = await client.get('/api/chat/pre-actions');
			return response.data;
		},

		async sendPromptFeedback(
			sessionId: string,
			messageId: string,
			actionIndex: number,
			verdict: 'approved' | 'rejected',
			reason?: string
		): Promise<APIResponse<void>> {
			const response = await client.post(
				`/api/chat/sessions/${sessionId}/messages/${messageId}/prompt-feedback`,
				{
					action_index: actionIndex,
					verdict,
					reason: reason || undefined
				}
			);
			return response.data;
		},

		// --- Persistent memory notes (Memory panel) ---

		async listMemory(
			params: { scope?: string; scope_ref?: string } = {}
		): Promise<APIResponse<{ notes: MemoryNote[]; injection: { cap_per_group: number; max_content_len: number } }>> {
			const search = new URLSearchParams();
			if (params.scope) search.append('scope', params.scope);
			if (params.scope_ref) search.append('scope_ref', params.scope_ref);
			const qs = search.toString();
			const response = await client.get(`/api/chat/memory${qs ? `?${qs}` : ''}`);
			return response.data;
		},

		async createMemory(body: {
			key: string;
			content: string;
			scope?: string;
			scope_ref?: string | null;
		}): Promise<APIResponse<MemoryNote>> {
			const response = await client.post('/api/chat/memory', body);
			return response.data;
		},

		async updateMemory(
			noteId: string,
			body: { key: string; content: string }
		): Promise<APIResponse<MemoryNote>> {
			const response = await client.put(`/api/chat/memory/${noteId}`, body);
			return response.data;
		},

		async deleteMemory(noteId: string): Promise<APIResponse<void>> {
			const response = await client.delete(`/api/chat/memory/${noteId}`);
			return response.data;
		}
	};
}
