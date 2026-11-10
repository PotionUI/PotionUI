import type { AxiosInstance } from 'axios';
import type {
	APIResponse,
	PresetInfo,
	PresetModeInfo,
	PresetPickerState,
	PresetConfigurationResponse,
	PresetFormOverridePatch,
	PresetFormOverridesResponse,
	PresetRequirementsResponse,
	PresetAdminDetail,
	PresetDisplayPayload,
	PresetMirrorCreatePayload,
	PresetMirrorModesPayload,
	PresetFixedPrompt
} from '$lib/types/api';
import type { PromptResourceSpec } from '$lib/utils/promptResources';
import type { PromptSyntaxSpec } from '$lib/utils/promptSyntax';

export function createPresetsApi(client: AxiosInstance) {
	return {
		async listPresets(includeUninstalled: boolean = false): Promise<APIResponse<PresetInfo[]>> {
			const params = includeUninstalled ? '?include_uninstalled=true' : '';
			const response = await client.get(`/api/presets${params}`);
			return response.data;
		},

		async getPreset(
			presetId: string
		): Promise<APIResponse<PresetInfo & { vars: Record<string, any> }>> {
			const response = await client.get(`/api/presets/${presetId}`);
			return response.data;
		},

		async getPresetPickerState(): Promise<APIResponse<PresetPickerState>> {
			const response = await client.get('/api/presets/picker-state');
			return response.data;
		},

		async pinPreset(presetId: string): Promise<APIResponse<PresetPickerState>> {
			const response = await client.put(`/api/presets/picker-state/pinned/${encodeURIComponent(presetId)}`);
			return response.data;
		},

		async unpinPreset(presetId: string): Promise<APIResponse<PresetPickerState>> {
			const response = await client.delete(`/api/presets/picker-state/pinned/${encodeURIComponent(presetId)}`);
			return response.data;
		},

		async pushRecentPreset(presetId: string): Promise<APIResponse<PresetPickerState>> {
			const response = await client.post(`/api/presets/picker-state/recent/${encodeURIComponent(presetId)}`);
			return response.data;
		},

		async getModeLabels(): Promise<APIResponse<{ labels: Record<string, string> }>> {
			const response = await client.get('/api/presets/mode-labels');
			return response.data;
		},

		async getPresetModes(
			presetId: string
		): Promise<
			APIResponse<{
				preset_id: string;
				modes: PresetModeInfo[];
				default_mode: string;
			}>
		> {
			const response = await client.get(`/api/presets/${presetId}/modes`);
			return response.data;
		},

		async getPresetFormSchema(
			presetId: string,
			mode?: string,
			formName?: string
		): Promise<
			APIResponse<{
				preset_id: string;
				form_schema: any;
				prompt_resources?: PromptResourceSpec[];
				prompt_syntax?: PromptSyntaxSpec[];
			}>
		> {
			const params = new URLSearchParams();
			if (mode) params.set('mode', mode);
			if (formName) params.set('form_name', formName);
			const query = params.toString();
			const response = await client.get(`/api/presets/${presetId}/form${query ? `?${query}` : ''}`);
			return response.data;
		},

		async getPromptResourceMembers(
			source: string,
			itemKey: string
		): Promise<APIResponse<{ members: Array<{ kind: 'image' | 'video' | 'audio'; name: string | null }> }>> {
			const response = await client.get(
				`/api/prompt-resources/sources/${encodeURIComponent(source)}/members?item=${encodeURIComponent(itemKey)}`
			);
			return response.data;
		},

		async reloadPreset(presetId: string): Promise<APIResponse<PresetInfo>> {
			const response = await client.post(`/api/presets/${presetId}/reload`);
			return response.data;
		},

		async getPresetConfiguration(
			presetId: string
		): Promise<APIResponse<PresetConfigurationResponse>> {
			const response = await client.get(`/api/presets/${presetId}/configuration`);
			return response.data;
		},

		async updatePresetConfiguration(
			presetId: string,
			values: Record<string, unknown>
		): Promise<APIResponse<PresetConfigurationResponse>> {
			const response = await client.put(`/api/presets/${presetId}/configuration`, { values });
			return response.data;
		},

		async getPresetFormOverrides(
			presetId: string,
			mode: string
		): Promise<APIResponse<PresetFormOverridesResponse>> {
			const params = new URLSearchParams({ mode });
			const response = await client.get(`/api/presets/${presetId}/form-overrides?${params.toString()}`);
			return response.data;
		},

		async updatePresetFormOverrides(
			presetId: string,
			mode: string,
			overrides: Record<string, PresetFormOverridePatch | null>
		): Promise<APIResponse<PresetFormOverridesResponse>> {
			const response = await client.put(`/api/presets/${presetId}/form-overrides`, { mode, overrides });
			return response.data;
		},

		async getPresetRequirements(
			presetId: string,
			options: { backendId?: string; refresh?: boolean } = {}
		): Promise<APIResponse<PresetRequirementsResponse>> {
			const params = new URLSearchParams();
			if (options.backendId) params.set('backend_id', options.backendId);
			if (options.refresh) params.set('refresh', '1');
			const query = params.toString();
			const response = await client.get(`/api/presets/${presetId}/requirements${query ? `?${query}` : ''}`);
			return response.data;
		},

		async getPresetAdmin(presetId: string): Promise<APIResponse<PresetAdminDetail>> {
			const response = await client.get(`/api/presets/${encodeURIComponent(presetId)}/admin`);
			return response.data;
		},

		async updatePresetDisplay(
			presetId: string,
			display: PresetDisplayPayload
		): Promise<APIResponse<PresetAdminDetail>> {
			const response = await client.put(`/api/presets/${encodeURIComponent(presetId)}/display`, display);
			return response.data;
		},

		async uploadPresetDisplayCover(presetId: string, file: File): Promise<APIResponse<PresetAdminDetail>> {
			const formData = new FormData();
			formData.append('file', file);
			const response = await client.post(`/api/presets/${encodeURIComponent(presetId)}/display/cover`, formData);
			return response.data;
		},

		async createPresetMirror(
			sourcePresetId: string,
			payload: PresetMirrorCreatePayload
		): Promise<APIResponse<PresetAdminDetail>> {
			const response = await client.post(`/api/presets/${encodeURIComponent(sourcePresetId)}/mirrors`, payload);
			return response.data;
		},

		async updatePresetMirrorModes(
			presetId: string,
			payload: PresetMirrorModesPayload
		): Promise<APIResponse<PresetAdminDetail>> {
			const response = await client.put(`/api/presets/${encodeURIComponent(presetId)}/modes`, payload);
			return response.data;
		},

		async getPresetFixedPrompt(presetId: string): Promise<APIResponse<PresetFixedPrompt>> {
			const response = await client.get(`/api/presets/${encodeURIComponent(presetId)}/fixed-prompt`);
			return response.data;
		},

		async updatePresetFixedPrompt(
			presetId: string,
			fixedPrompt: PresetFixedPrompt
		): Promise<APIResponse<PresetFixedPrompt>> {
			const response = await client.put(`/api/presets/${encodeURIComponent(presetId)}/fixed-prompt`, fixedPrompt);
			return response.data;
		}
	};
}
