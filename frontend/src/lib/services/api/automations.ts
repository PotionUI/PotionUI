import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import type {
	Automation,
	AutomationExportEnvelope,
	AutomationGraph,
	AutomationImportResult,
	AutomationRun,
	AutomationRunDetail,
	AutomationTemplate,
	CreateAutomationInput,
	ListRunsOptions,
	NodeTypeDef,
	RunAutomationInput,
	UpdateAutomationInput,
	ValidationIssue
} from '$lib/types/automations';

export function createAutomationsApi(client: AxiosInstance) {
	return {
		async listAutomations(): Promise<APIResponse<Automation[]>> {
			const response = await client.get('/api/automations');
			return response.data;
		},

		async getAutomation(automationId: string): Promise<APIResponse<Automation>> {
			const response = await client.get(`/api/automations/${automationId}`);
			return response.data;
		},

		async createAutomation(input: CreateAutomationInput): Promise<APIResponse<Automation>> {
			const response = await client.post('/api/automations', input);
			return response.data;
		},

		async updateAutomation(
			automationId: string,
			input: UpdateAutomationInput
		): Promise<APIResponse<Automation>> {
			const response = await client.put(`/api/automations/${automationId}`, input);
			return response.data;
		},

		async deleteAutomation(automationId: string): Promise<APIResponse<null>> {
			const response = await client.delete(`/api/automations/${automationId}`);
			return response.data;
		},

		async enableAutomation(automationId: string): Promise<APIResponse<Automation>> {
			const response = await client.patch(`/api/automations/${automationId}/enable`);
			return response.data;
		},

		async disableAutomation(automationId: string): Promise<APIResponse<Automation>> {
			const response = await client.patch(`/api/automations/${automationId}/disable`);
			return response.data;
		},

		async runAutomation(
			automationId: string,
			input: RunAutomationInput = {}
		): Promise<APIResponse<AutomationRun>> {
			const response = await client.post(`/api/automations/${automationId}/run`, input);
			return response.data;
		},

		async validateGraph(graph: AutomationGraph): Promise<APIResponse<ValidationIssue[]>> {
			const response = await client.post('/api/automations/validate', { graph });
			return response.data;
		},

		async listNodeTypes(): Promise<APIResponse<NodeTypeDef[]>> {
			const response = await client.get('/api/automations/node-types');
			return response.data;
		},

		async listAutomationTemplates(): Promise<APIResponse<AutomationTemplate[]>> {
			const response = await client.get('/api/automations/templates');
			return response.data;
		},

		async instantiateAutomationTemplate(
			templateKey: string,
			name?: string
		): Promise<APIResponse<AutomationImportResult>> {
			const response = await client.post(
				`/api/automations/templates/${encodeURIComponent(templateKey)}/instantiate`,
				{ name }
			);
			return response.data;
		},

		async listRuns(
			automationId: string,
			options: ListRunsOptions = {}
		): Promise<APIResponse<AutomationRun[]>> {
			const params = new URLSearchParams();
			if (options.limit) params.set('limit', String(options.limit));
			if (options.before) params.set('before', options.before);
			const query = params.toString();
			const response = await client.get(
				`/api/automations/${automationId}/runs${query ? `?${query}` : ''}`
			);
			return response.data;
		},

		async getRun(automationId: string, runId: string): Promise<APIResponse<AutomationRunDetail>> {
			const response = await client.get(`/api/automations/${automationId}/runs/${runId}`);
			return response.data;
		},

		async exportAutomation(
			automationId: string
		): Promise<APIResponse<AutomationExportEnvelope>> {
			const response = await client.get(`/api/automations/${automationId}/export`);
			return response.data;
		},

		async importAutomation(
			document: AutomationExportEnvelope,
			name?: string
		): Promise<APIResponse<AutomationImportResult>> {
			const response = await client.post('/api/automations/import', { document, name });
			return response.data;
		}
	};
}
