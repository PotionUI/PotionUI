import type { AxiosInstance } from 'axios';
import type { APIResponse } from '$lib/types/api';
import type {
	CreateSavedSegmentInput,
	CreateSegmentTemplateInput,
	SavedSegment,
	SegmentCategory,
	SegmentTemplate,
	UpdateSavedSegmentInput,
	UpdateSegmentTemplateInput
} from '$lib/types/segments';

export function createSegmentsApi(client: AxiosInstance) {
	return {
		async listSavedSegments(categoryId?: string): Promise<APIResponse<{ segments: SavedSegment[] }>> {
			const response = await client.get('/api/segments', {
				params: categoryId ? { category_id: categoryId } : undefined
			});
			return response.data;
		},
		async createSavedSegment(data: CreateSavedSegmentInput): Promise<APIResponse<SavedSegment>> {
			const response = await client.post('/api/segments', data);
			return response.data;
		},
		async updateSavedSegment(id: string, data: UpdateSavedSegmentInput): Promise<APIResponse<SavedSegment>> {
			const response = await client.put(`/api/segments/${id}`, data);
			return response.data;
		},
		async deleteSavedSegment(id: string): Promise<APIResponse<unknown>> {
			const response = await client.delete(`/api/segments/${id}`);
			return response.data;
		},

		async listSegmentTemplates(): Promise<APIResponse<{ templates: SegmentTemplate[] }>> {
			const response = await client.get('/api/segment-templates');
			return response.data;
		},
		async createSegmentTemplate(data: CreateSegmentTemplateInput): Promise<APIResponse<SegmentTemplate>> {
			const response = await client.post('/api/segment-templates', data);
			return response.data;
		},
		async updateSegmentTemplate(id: string, data: UpdateSegmentTemplateInput): Promise<APIResponse<SegmentTemplate>> {
			const response = await client.put(`/api/segment-templates/${id}`, data);
			return response.data;
		},
		async deleteSegmentTemplate(id: string): Promise<APIResponse<unknown>> {
			const response = await client.delete(`/api/segment-templates/${id}`);
			return response.data;
		},

		async listSegmentCategories(): Promise<APIResponse<{ categories: SegmentCategory[] }>> {
			const response = await client.get('/api/segment-categories');
			return response.data;
		},
		async createSegmentCategory(
			data: Pick<SegmentCategory, 'name' | 'description' | 'color'>
		): Promise<APIResponse<SegmentCategory>> {
			const response = await client.post('/api/segment-categories', data);
			return response.data;
		},
		async updateSegmentCategory(
			id: string,
			data: Pick<SegmentCategory, 'name' | 'description' | 'color'>
		): Promise<APIResponse<SegmentCategory>> {
			const response = await client.put(`/api/segment-categories/${id}`, data);
			return response.data;
		},
		async deleteSegmentCategory(id: string): Promise<APIResponse<unknown>> {
			const response = await client.delete(`/api/segment-categories/${id}`);
			return response.data;
		}
	};
}
