import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';

export interface PipeInstallState {
	name: string;
	status: 'not_installed' | 'installing' | 'installed' | 'error';
	requirements: Record<string, unknown>;
	/** Non-null: the pipe cannot be installed from here, and these are the commands that do it. */
	manual_install: string | null;
	error: string | null;
}

/**
 * A pipe's registry key can contain a slash (`generator/some_family`), and
 * the backend routes on `:path` to accept it - so the segments are encoded
 * individually and the separators left alone.
 */
function pipePath(name: string): string {
	return name.split('/').map(encodeURIComponent).join('/');
}

export function createPipesApi(client: AxiosInstance) {
	return {
		async installPipe(name: string): Promise<APIResponse<PipeInstallState>> {
			const response = await client.post(`/api/pipes/${pipePath(name)}/install`);
			return response.data;
		}
	};
}
