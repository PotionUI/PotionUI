import type { AxiosInstance } from 'axios';
import { api } from '$lib/services/api/index';
import type { APIResponse } from '$lib/types/api';
import type {
	ClipPreviewBody,
	ClipPreviewStart,
	EffectAnalysisBody,
	FramePreviewBody,
	FramePreviewResult,
	MediaToolArtifact,
	MediaToolRun,
	PreviewMediaToolBody,
	StartMediaToolRunBody,
	ToolDTO,
	ToolMediaKind,
	ToolScope
} from '$lib/media-tools/types';

function unwrap<T>(body: APIResponse<T> | undefined, fallback: string): T {
	if (!body || !body.success || body.data === undefined) {
		throw new Error(body?.message || body?.error || fallback);
	}
	return body.data;
}

export function createMediaToolsApi(client: AxiosInstance) {
	return {
		async listMediaTools(kind?: ToolMediaKind, scope?: ToolScope): Promise<ToolDTO[]> {
			const params: Record<string, string> = {};
			if (kind) params.kind = kind;
			if (scope) params.scope = scope;
			const response = await client.get<APIResponse<{ tools: ToolDTO[] }>>('/api/media-tools', {
				params
			});
			return unwrap(response.data, 'The tool list could not be loaded').tools ?? [];
		},

		async startMediaToolRun(body: StartMediaToolRunBody): Promise<{ run_id: string }> {
			const response = await client.post<APIResponse<{ run_id: string }>>(
				'/api/media-tools/runs',
				body
			);
			return unwrap(response.data, 'The edit could not be started');
		},

		async getMediaToolRun(runId: string, signal?: AbortSignal): Promise<MediaToolRun> {
			const response = await client.get<APIResponse<MediaToolRun>>(
				`/api/media-tools/runs/${encodeURIComponent(runId)}`,
				{ signal }
			);
			return unwrap(response.data, 'The edit status could not be read');
		},

		async cancelMediaToolRun(runId: string): Promise<MediaToolRun | null> {
			const response = await client.delete<APIResponse<MediaToolRun>>(
				`/api/media-tools/runs/${encodeURIComponent(runId)}`
			);
			return response.data?.data ?? null;
		},

		async previewMediaTool(body: PreviewMediaToolBody, signal?: AbortSignal): Promise<Blob> {
			const response = await client.post<Blob>('/api/media-tools/preview', body, {
				responseType: 'blob',
				signal
			});
			return response.data;
		},

		async listMediaToolRuns(): Promise<MediaToolRun[]> {
			const response = await client.get<APIResponse<{ runs: MediaToolRun[] }>>('/api/media-tools/runs');
			return unwrap(response.data, 'The edits in progress could not be read').runs ?? [];
		},

		async previewFrame(body: FramePreviewBody, signal?: AbortSignal): Promise<FramePreviewResult> {
			const response = await client.post<Blob>('/api/media-tools/preview/frame', body, {
				responseType: 'blob',
				signal
			});
			return { blob: response.data, cached: response.headers?.['x-preview-cache'] === 'hit' };
		},

		async startClipPreview(body: ClipPreviewBody): Promise<ClipPreviewStart> {
			const response = await client.post<APIResponse<ClipPreviewStart>>(
				'/api/media-tools/preview/clip',
				body
			);
			return unwrap(response.data, 'The preview could not be started');
		},

		async uploadMediaToolArtifact(file: File): Promise<MediaToolArtifact> {
			const formData = new FormData();
			formData.append('file', file);
			const response = await client.post<APIResponse<MediaToolArtifact>>(
				'/api/media-tools/artifacts',
				formData
			);
			return unwrap(response.data, 'The file could not be saved');
		},

		async effectShader(url: string, signal?: AbortSignal): Promise<string> {
			const response = await client.get<string>(url, { responseType: 'text', transformResponse: (data) => data, signal });
			if (typeof response.data !== 'string') throw new Error('The effect preview could not be loaded');
			return response.data;
		},

		async effectAnalysis(body: EffectAnalysisBody, signal?: AbortSignal): Promise<Record<string, number>> {
			const response = await client.post<APIResponse<{ values: Record<string, number> }>>(
				'/api/media-tools/effects/analysis',
				body,
				{ signal }
			);
			return unwrap(response.data, 'The clip could not be measured').values ?? {};
		}
	};
}

export type MediaToolsApi = ReturnType<typeof createMediaToolsApi>;

function bound(): MediaToolsApi {
	return createMediaToolsApi(api.getClient());
}

export async function listMediaTools(kind?: ToolMediaKind, scope?: ToolScope): Promise<ToolDTO[]> {
	return bound().listMediaTools(kind, scope);
}

export async function startMediaToolRun(body: StartMediaToolRunBody): Promise<{ run_id: string }> {
	return bound().startMediaToolRun(body);
}

export async function getMediaToolRun(runId: string, signal?: AbortSignal): Promise<MediaToolRun> {
	return bound().getMediaToolRun(runId, signal);
}

export async function cancelMediaToolRun(runId: string): Promise<MediaToolRun | null> {
	return bound().cancelMediaToolRun(runId);
}

export async function previewMediaTool(
	body: PreviewMediaToolBody,
	signal?: AbortSignal
): Promise<Blob> {
	return bound().previewMediaTool(body, signal);
}

export async function listMediaToolRuns(): Promise<MediaToolRun[]> {
	return bound().listMediaToolRuns();
}

export async function previewFrame(
	body: FramePreviewBody,
	signal?: AbortSignal
): Promise<FramePreviewResult> {
	return bound().previewFrame(body, signal);
}

export async function startClipPreview(body: ClipPreviewBody): Promise<ClipPreviewStart> {
	return bound().startClipPreview(body);
}

export async function uploadMediaToolArtifact(file: File): Promise<MediaToolArtifact> {
	return bound().uploadMediaToolArtifact(file);
}

export async function effectShader(url: string, signal?: AbortSignal): Promise<string> {
	return bound().effectShader(url, signal);
}

export async function effectAnalysis(body: EffectAnalysisBody, signal?: AbortSignal): Promise<Record<string, number>> {
	return bound().effectAnalysis(body, signal);
}
