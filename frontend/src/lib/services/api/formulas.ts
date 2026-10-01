import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import { normalizePlan } from '$lib/formulas/normalize';
import type { Formula, FormulaContent, FormulaDeclaration, FormulaDraft, LoraMode, ServerPlan } from '$lib/formulas/types';

export function createFormulasApi(client: AxiosInstance) {
	return {
		async listFormulas(presetId: string, mode: string): Promise<APIResponse<Formula[]>> {
			const response = await client.get('/api/formulas', { params: { preset_id: presetId, mode } });
			return response.data;
		},

		async createFormula(draft: FormulaDraft): Promise<APIResponse<Formula>> {
			const response = await client.post('/api/formulas', draft);
			return response.data;
		},

		async updateFormula(
			formulaId: string,
			patch: { name?: string; note?: string | null; content?: FormulaContent }
		): Promise<APIResponse<Formula>> {
			const response = await client.put(`/api/formulas/${formulaId}`, patch);
			return response.data;
		},

		async duplicateFormula(formulaId: string): Promise<APIResponse<Formula>> {
			const response = await client.post(`/api/formulas/${formulaId}/duplicate`);
			return response.data;
		},

		async deleteFormula(formulaId: string): Promise<APIResponse<{ message?: string }>> {
			const response = await client.delete(`/api/formulas/${formulaId}`);
			return response.data;
		},

		async planFormula(
			formulaId: string,
			request: { form_name: string | null; current_values: Record<string, unknown>; lora_mode: LoraMode }
		): Promise<APIResponse<ServerPlan>> {
			const response = await client.post(`/api/formulas/${formulaId}/plan`, request);
			const body = response.data as APIResponse<Record<string, unknown>>;
			return { ...body, data: body.success ? normalizePlan(body.data) : undefined } as APIResponse<ServerPlan>;
		},

		async getFormulaDeclaration(
			presetId: string,
			mode: string,
			formName?: string
		): Promise<FormulaDeclaration | null> {
			const params: Record<string, string> = { mode };
			if (formName) params.form_name = formName;
			const response = await client.get(`/api/presets/${presetId}/form`, { params });
			const block = response.data?.data?.formulas as FormulaDeclaration | undefined;
			return block && Array.isArray(block.groups) && block.groups.length > 0 ? block : null;
		}
	};
}
