import { describe, it, expect } from 'vitest';
import { buildModelPickerEntries, downloadPayloadForRecommendation } from './modelRecommendations';
import type { ModelRecommendation } from '$lib/types/api';

describe('buildModelPickerEntries', () => {
	it('returns plain model entries when there are no recommendations', () => {
		const models = [{ id: '1', name: 'A' }, { id: '2', name: 'B' }];
		const entries = buildModelPickerEntries(models, null);
		expect(entries).toEqual([
			{ kind: 'model', model: models[0] },
			{ kind: 'model', model: models[1] }
		]);
	});

	it('pins a matched recommendation first as a selectable model', () => {
		const models = [
			{ id: '1', name: 'Other Model' },
			{ id: '2', name: 'Great Checkpoint', filename: 'great_checkpoint.safetensors' }
		];
		const recommendations: ModelRecommendation[] = [
			{ name: 'Great Checkpoint', installed: true, provider: 'civitai', ref: 'abc' }
		];
		const entries = buildModelPickerEntries(models, recommendations);
		expect(entries[0]).toEqual({
			kind: 'recommended-model',
			recommendation: recommendations[0],
			model: models[1]
		});
		// The matched model is not duplicated further down the list.
		expect(entries).toHaveLength(2);
		expect(entries[1]).toEqual({ kind: 'model', model: models[0] });
	});

	it('matches by filename stem when the display name differs', () => {
		const models = [{ id: '1', name: 'Different Label', filename: 'sdxl_realistic_v2.safetensors' }];
		const recommendations: ModelRecommendation[] = [
			{ name: 'sdxl_realistic_v2', installed: true, provider: 'civitai', ref: 'abc' }
		];
		const entries = buildModelPickerEntries(models, recommendations);
		expect(entries[0].kind).toBe('recommended-model');
	});

	it('renders an unmatched recommendation as a download offer', () => {
		const models = [{ id: '1', name: 'Unrelated' }];
		const recommendations: ModelRecommendation[] = [
			{ name: 'Missing Model', installed: false, link: 'https://example.com/model.safetensors' }
		];
		const entries = buildModelPickerEntries(models, recommendations);
		expect(entries[0]).toEqual({ kind: 'recommended-download', recommendation: recommendations[0] });
		expect(entries[1]).toEqual({ kind: 'model', model: models[0] });
	});
});

describe('downloadPayloadForRecommendation', () => {
	it('builds a provider-backed payload', () => {
		const recommendation: ModelRecommendation = {
			name: 'Great Checkpoint',
			installed: false,
			provider: 'civitai',
			ref: '12345'
		};
		expect(downloadPayloadForRecommendation(recommendation, 'checkpoint')).toEqual({
			name: 'Great Checkpoint',
			model_type: 'checkpoint',
			provider: 'civitai',
			ref: '12345'
		});
	});

	it('builds a direct-link payload', () => {
		const recommendation: ModelRecommendation = {
			name: 'Great Checkpoint',
			installed: false,
			link: 'https://example.com/model.safetensors',
			sha256: 'deadbeef'
		};
		expect(downloadPayloadForRecommendation(recommendation, 'checkpoint')).toEqual({
			name: 'Great Checkpoint',
			model_type: 'checkpoint',
			link: 'https://example.com/model.safetensors',
			sha256: 'deadbeef'
		});
	});
});
