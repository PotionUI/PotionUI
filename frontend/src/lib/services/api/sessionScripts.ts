import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import type { ScriptTemplate } from '$lib/session/scriptExamples';

export function createSessionScriptsApi(client: AxiosInstance) {
	return {
		async getSessionScriptTemplate(sessionId: string, mode?: string): Promise<APIResponse<ScriptTemplate>> {
			const response = await client.get(`/api/sessions/${encodeURIComponent(sessionId)}/request`, {
				params: mode ? { mode } : undefined
			});
			return response.data;
		}
	};
}
