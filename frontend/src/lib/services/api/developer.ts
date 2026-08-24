import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';

export function createDeveloperApi(client: AxiosInstance) {
	return {
		async getDeveloperTemplateFunctions(): Promise<
			APIResponse<{ functions: any[]; total: number; categories: string[] }>
		> {
			const response = await client.get('/api/developer/template-functions');
			return response.data;
		}
	};
}
