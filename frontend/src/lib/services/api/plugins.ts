import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import type { PluginMediaToolEntry } from '$lib/tools/tools';

export function createPluginsApi(client: AxiosInstance) {
	return {
		async getHistoryTools(): Promise<APIResponse<PluginMediaToolEntry[]>> {
			const response = await client.get('/api/plugins/history-tools');
			return response.data;
		}
	};
}
