import type { AxiosInstance } from 'axios';
import type {
	APIResponse,
	PresetInfo,
	PresetModeInfo,
	PresetConfigurationResponse,
	PresetFormOverridePatch,
	PresetFormOverridesResponse
} from '$lib/types/api';

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
		): Promise<APIResponse<{ preset_id: string; form_schema: any }>> {
			const params = new URLSearchParams();
			if (mode) params.set('mode', mode);
			if (formName) params.set('form_name', formName);
			const query = params.toString();
			const response = await client.get(`/api/presets/${presetId}/form${query ? `?${query}` : ''}`);
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
		}
	};
}
