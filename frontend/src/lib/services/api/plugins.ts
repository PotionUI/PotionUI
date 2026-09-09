import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import type { PluginHistoryToolEntry } from '$lib/history/tools';

export function createPluginsApi(client: AxiosInstance) {
	return {
		async getHistoryTools(): Promise<APIResponse<PluginHistoryToolEntry[]>> {
			const response = await client.get('/api/plugins/history-tools');
			return response.data;
		}
	};
}
