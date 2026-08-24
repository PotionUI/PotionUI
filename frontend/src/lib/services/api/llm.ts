import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import type { LLMConfig, UserToolPreference } from '$lib/types/llm';

export function createLlmApi(client: AxiosInstance) {
	return {
		async getLLMConfigurations(): Promise<APIResponse<{ configurations: LLMConfig[] }>> {
			const response = await client.get('/api/llm/configurations');
			return response.data;
		},

		async getMyLLMConfigurations(): Promise<APIResponse<{ llm_configs: LLMConfig[] }>> {
			const response = await client.get('/api/llm/configurations/my');
			return response.data;
		},

		/**
		 * The tools the current user may see, with their own (global) opt-out
		 * state, scoped to `llmConfigId` (the chat session's active config) -
		 * admin-disabled tools for that config are omitted and `locked`
		 * reflects that config's rows.
		 */
		async getMyToolsetPreferences(llmConfigId: string): Promise<APIResponse<UserToolPreference[]>> {
			const response = await client.get('/api/llm/toolset/preferences', {
				params: { llm_config_id: llmConfigId }
			});
			return response.data;
		},

		/**
		 * Toggle the user's own (global) opt-out for a tool, rejected (403/409)
		 * if `llmConfigId` (the caller's active config) disabled or locked the
		 * tool - the opt-out itself is always stored globally either way.
		 */
		async updateMyToolPreference(
			toolName: string,
			disabled: boolean,
			llmConfigId: string
		): Promise<APIResponse<{ name: string; disabled_by_user: boolean }>> {
			const response = await client.put(`/api/llm/toolset/preferences/${toolName}`, {
				disabled,
				llm_config_id: llmConfigId
			});
			return response.data;
		}
	};
}
