import type { ModelRecommendation } from '$lib/types/api';

export interface RecommendedModelEntry {
	kind: 'recommended-model';
	recommendation: ModelRecommendation;
	model: any;
}

export interface RecommendedDownloadEntry {
	kind: 'recommended-download';
	recommendation: ModelRecommendation;
}

export interface PlainModelEntry {
	kind: 'model';
	model: any;
}

export type ModelPickerEntry = RecommendedModelEntry | RecommendedDownloadEntry | PlainModelEntry;

function normalize(value: string): string {
	return value.trim().toLowerCase();
}

function stem(filename: string): string {
	return filename.replace(/\.[^/.]+$/, '');
}

function findMatchingModel(recommendation: ModelRecommendation, models: any[]): any | undefined {
	const target = normalize(recommendation.name);
	if (!target) return undefined;
	return models.find((model) => {
		const name = typeof model?.name === 'string' ? normalize(model.name) : '';
		const filename = typeof model?.filename === 'string' ? model.filename : '';
		return name === target || normalize(filename) === target || normalize(stem(filename)) === target;
	});
}

/**
 * Orders a model picker's options with recommendations first. A recommendation whose
 * name matches an already-fetched model is rendered as that real, selectable model
 * (still badged); one that doesn't match anything installed is rendered as a download
 * offer. Remaining models follow in their original order, without duplicating any
 * model already surfaced as a recommendation match.
 */
export function buildModelPickerEntries(
	models: any[],
	recommendations: ModelRecommendation[] | null | undefined
): ModelPickerEntry[] {
	if (!recommendations || recommendations.length === 0) {
		return models.map((model) => ({ kind: 'model', model }));
	}

	const matchedIds = new Set<string>();
	const recommended: ModelPickerEntry[] = recommendations.map((recommendation) => {
		const model = findMatchingModel(recommendation, models);
		if (model) {
			if (model.id) matchedIds.add(model.id);
			return { kind: 'recommended-model', recommendation, model };
		}
		return { kind: 'recommended-download', recommendation };
	});

	const rest: ModelPickerEntry[] = models
		.filter((model) => !model.id || !matchedIds.has(model.id))
		.map((model) => ({ kind: 'model', model }));

	return [...recommended, ...rest];
}

/** Builds the `POST /api/models/downloads` body for a recommendation, per its source shape. */
export function downloadPayloadForRecommendation(
	recommendation: ModelRecommendation,
	modelType: string
): { name: string; model_type: string; provider?: string; ref?: string; link?: string; sha256?: string } {
	const base = { name: recommendation.name, model_type: modelType };
	if ('provider' in recommendation && recommendation.provider) {
		return { ...base, provider: recommendation.provider, ref: recommendation.ref };
	}
	return { ...base, link: (recommendation as any).link, sha256: (recommendation as any).sha256 };
}
