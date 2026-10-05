import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import type {
	OrganizeActivityPage,
	OrganizeAdminOverview,
	OrganizeAdminUser,
	OrganizeCatalog,
	OrganizeCollectionRuleRef,
	OrganizeCollectionScope,
	OrganizeJob,
	OrganizeOption,
	OrganizePreview,
	OrganizePreviewRequest,
	OrganizeProvenance,
	OrganizeRule,
	OrganizeRuleInput,
	OrganizeSubject,
	OrganizeSummary,
	OrganizeUndoResult
} from '$lib/types/organize';

export function createOrganizeApi(client: AxiosInstance) {
	return {
		async getOrganizeCatalog(subject?: OrganizeSubject): Promise<APIResponse<OrganizeCatalog>> {
			const response = await client.get('/api/organize/catalog', { params: subject ? { subject } : {} });
			return response.data;
		},

		async getOrganizeFactOptions(
			key: string,
			params: { subject: OrganizeSubject; q?: string; limit?: number }
		): Promise<APIResponse<OrganizeOption[]>> {
			const response = await client.get(`/api/organize/facts/${encodeURIComponent(key)}/options`, { params });
			return response.data;
		},

		async getOrganizeSummary(): Promise<APIResponse<OrganizeSummary>> {
			const response = await client.get('/api/organize/summary');
			return response.data;
		},

		async listOrganizeRules(subject?: OrganizeSubject): Promise<APIResponse<OrganizeRule[]>> {
			const response = await client.get('/api/organize/rules', { params: subject ? { subject } : {} });
			return response.data;
		},

		async getOrganizeRule(ruleId: string): Promise<APIResponse<OrganizeRule>> {
			const response = await client.get(`/api/organize/rules/${ruleId}`);
			return response.data;
		},

		async createOrganizeRule(
			input: Partial<OrganizeRuleInput> & { name: string; subject: OrganizeSubject }
		): Promise<APIResponse<OrganizeRule>> {
			const response = await client.post('/api/organize/rules', input);
			return response.data;
		},

		async updateOrganizeRule(
			ruleId: string,
			input: Omit<OrganizeRuleInput, 'subject'>
		): Promise<APIResponse<OrganizeRule>> {
			const response = await client.put(`/api/organize/rules/${ruleId}`, input);
			return response.data;
		},

		async patchOrganizeRule(
			ruleId: string,
			patch: Partial<{ enabled: boolean; name: string; stop_after: boolean }>
		): Promise<APIResponse<OrganizeRule>> {
			const response = await client.patch(`/api/organize/rules/${ruleId}`, patch);
			return response.data;
		},

		async deleteOrganizeRule(ruleId: string): Promise<APIResponse<{ deleted: boolean }>> {
			const response = await client.delete(`/api/organize/rules/${ruleId}`);
			return response.data;
		},

		async reorderOrganizeRules(subject: OrganizeSubject, ruleIds: string[]): Promise<APIResponse<OrganizeRule[]>> {
			const response = await client.post('/api/organize/rules/reorder', { subject, rule_ids: ruleIds });
			return response.data;
		},

		async duplicateOrganizeRule(ruleId: string): Promise<APIResponse<OrganizeRule>> {
			const response = await client.post(`/api/organize/rules/${ruleId}/duplicate`);
			return response.data;
		},

		async previewOrganizeRule(body: OrganizePreviewRequest): Promise<APIResponse<OrganizePreview>> {
			const response = await client.post('/api/organize/preview', body);
			return response.data;
		},

		async applyOrganizeRuleToExisting(ruleId: string): Promise<APIResponse<OrganizeJob>> {
			const response = await client.post(`/api/organize/rules/${ruleId}/apply-existing`, {});
			return response.data;
		},

		async listOrganizeJobs(active?: boolean): Promise<APIResponse<OrganizeJob[]>> {
			const response = await client.get('/api/organize/jobs', { params: active ? { active: true } : {} });
			return response.data;
		},

		async getOrganizeJob(jobId: string): Promise<APIResponse<OrganizeJob>> {
			const response = await client.get(`/api/organize/jobs/${jobId}`);
			return response.data;
		},

		async cancelOrganizeJob(jobId: string): Promise<APIResponse<OrganizeJob>> {
			const response = await client.post(`/api/organize/jobs/${jobId}/cancel`);
			return response.data;
		},

		async getOrganizeActivity(params: {
			subject?: OrganizeSubject;
			rule_id?: string;
			limit?: number;
			before?: string;
		}): Promise<APIResponse<OrganizeActivityPage>> {
			const response = await client.get('/api/organize/activity', { params });
			return response.data;
		},

		async undoOrganizeRun(runId: string): Promise<APIResponse<OrganizeUndoResult>> {
			const response = await client.post(`/api/organize/runs/${runId}/undo`);
			return response.data;
		},

		async getOrganizeProvenance(
			itemType: OrganizeSubject,
			itemId: string
		): Promise<APIResponse<OrganizeProvenance[]>> {
			const response = await client.get(`/api/organize/provenance/${itemType}/${itemId}`);
			return response.data;
		},

		async getOrganizeCollectionRules(
			scope: OrganizeCollectionScope,
			collectionId: string
		): Promise<APIResponse<OrganizeCollectionRuleRef[]>> {
			const response = await client.get(`/api/organize/collections/${scope}/${collectionId}/rules`);
			return response.data;
		},

		async getOrganizeAdminOverview(): Promise<APIResponse<OrganizeAdminOverview>> {
			const response = await client.get('/api/admin/organize/overview');
			return response.data;
		},

		async updateOrganizeAdminControls(
			body: Partial<{ paused_all: boolean; default_rule_cap: number; hourly_limit: number }>
		): Promise<APIResponse<OrganizeAdminOverview>> {
			const response = await client.put('/api/admin/organize/controls', body);
			return response.data;
		},

		async updateOrganizeAdminUser(
			userId: string,
			body: Partial<{ paused: boolean; rule_cap: number | null }>
		): Promise<APIResponse<OrganizeAdminUser>> {
			const response = await client.put(`/api/admin/organize/users/${userId}`, body);
			return response.data;
		}
	};
}
