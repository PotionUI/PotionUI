import type { AxiosInstance } from 'axios';
import type {
	APIResponse,
	Session,
	SaveSessionRequest,
	UpdateSessionRequest,
	SessionVersionSummary,
	SessionVersionDetail
} from '$lib/types/api';
import { SESSION_CLIENT_HEADER } from '$lib/session/clientIdentity';

function clientHeaders(clientId: string | undefined) {
	return clientId ? { headers: { [SESSION_CLIENT_HEADER]: clientId } } : undefined;
}

export function createSessionsApi(client: AxiosInstance) {
	const sessionRequests = new Map<string, Promise<APIResponse<Session>>>();

	return {
		async getSessionsForPreset(presetId: string): Promise<APIResponse<Session[]>> {
			const response = await client.get(`/api/sessions/preset/${presetId}`);
			return response.data;
		},

		async getSessionById(sessionId: string): Promise<APIResponse<Session>> {
			const existing = sessionRequests.get(sessionId);
			if (existing) return existing;
			const request = client
				.get(`/api/sessions/${sessionId}`)
				.then((response) => response.data as APIResponse<Session>)
				.finally(() => sessionRequests.delete(sessionId));
			sessionRequests.set(sessionId, request);
			return request;
		},

		async saveSession(request: SaveSessionRequest, clientId?: string): Promise<APIResponse<Session>> {
			const response = await client.post('/api/sessions/save', request, clientHeaders(clientId));
			return response.data;
		},

		async updateSession(
			sessionId: string,
			request: UpdateSessionRequest,
			clientId?: string
		): Promise<APIResponse<Session>> {
			const response = await client.put(`/api/sessions/${sessionId}`, request, clientHeaders(clientId));
			return response.data;
		},

		async setSessionPinned(sessionId: string, pinned: boolean): Promise<APIResponse<Session>> {
			const response = await client.put(`/api/sessions/${sessionId}/pin`, { pinned });
			return response.data;
		},

		async deleteSession(sessionId: string): Promise<APIResponse<{ message: string }>> {
			const response = await client.delete(`/api/sessions/${sessionId}`);
			return response.data;
		},

		// Session history — newest first, [] when the session has no prior saves.
		async getSessionVersions(
			sessionId: string,
			page?: { limit?: number; before?: number }
		): Promise<APIResponse<SessionVersionSummary[]>> {
			const response = await client.get(`/api/sessions/${sessionId}/versions`, { params: page });
			return response.data;
		},

		async getSessionVersion(
			sessionId: string,
			versionNumber: number
		): Promise<APIResponse<SessionVersionDetail>> {
			const response = await client.get(`/api/sessions/${sessionId}/versions/${versionNumber}`);
			return response.data;
		}
	};
}
