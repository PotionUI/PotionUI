import type { DiscoveredLLMModel } from '$lib/types/llm';

export type MatchPart = { text: string; match: boolean };

function searchableText(model: DiscoveredLLMModel): string {
	const details = model.details ? Object.values(model.details).join(' ') : '';
	return `${model.id} ${model.label ?? ''} ${details}`.toLowerCase();
}

export function filterModels(models: DiscoveredLLMModel[], query: string): DiscoveredLLMModel[] {
	const needle = query.trim().toLowerCase();
	if (!needle) return models;
	return models.filter((model) => searchableText(model).includes(needle));
}

export function splitMatch(text: string, query: string): MatchPart[] {
	const needle = query.trim().toLowerCase();
	if (!needle) return [{ text, match: false }];
	const index = text.toLowerCase().indexOf(needle);
	if (index < 0) return [{ text, match: false }];
	return [
		{ text: text.slice(0, index), match: false },
		{ text: text.slice(index, index + needle.length), match: true },
		{ text: text.slice(index + needle.length), match: false }
	].filter((part) => part.text.length > 0);
}

export function formatModelSize(bytes: number | null | undefined): string | null {
	if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes <= 0) return null;
	const units = ['B', 'KB', 'MB', 'GB', 'TB'];
	let value = bytes;
	let unit = 0;
	while (value >= 1024 && unit < units.length - 1) {
		value /= 1024;
		unit += 1;
	}
	return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function modelMeta(model: DiscoveredLLMModel): string[] {
	const details = model.details ?? {};
	return [details.parameter_size, details.quantization, formatModelSize(model.size), details.owned_by].filter(
		(part): part is string => typeof part === 'string' && part.length > 0
	);
}
