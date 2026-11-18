import { get } from 'svelte/store';
import type { MediaToolInputRef } from '$lib/media-tools/types';
import type { EditStep } from '$lib/tool-tabs/frame/editStack';
import { GENERATE_TOOL, chosenResult, readGenerateParams, type GenerateBasis, type GenerateParams, type GenerateResult } from '../image/generate/generateStep';
import { runViews } from '../image/generate/runEngine';

export interface ChainBase {
	step: EditStep<unknown>;
	params: GenerateParams;
	result: GenerateResult;
}

export interface Rebased {
	input: MediaToolInputRef | null;
	url: string | null;
	base: ChainBase | null;
	tail: EditStep<unknown>[];
	stale: ReadonlySet<string>;
	left: ReadonlySet<string>;
}

export function refKey(ref: MediaToolInputRef | null): string {
	if (!ref) return '';
	if ('item_id' in ref && ref.item_id) return `i:${ref.item_id}`;
	if ('generation_file_id' in ref && ref.generation_file_id) return `g:${ref.generation_file_id}`;
	return '';
}

export function chainSignature(key: string, tail: readonly EditStep<unknown>[]): string {
	return JSON.stringify([key, tail.filter((step) => !step.hidden).map((step) => [step.tool, step.params])]);
}

function statusOf(params: GenerateParams): string {
	return get(runViews)[params.run.id]?.status ?? params.run.status;
}

export function rebase(steps: readonly EditStep<unknown>[], original: MediaToolInputRef | null): Rebased {
	let input: MediaToolInputRef | null = original;
	let url: string | null = null;
	let key = refKey(original);
	let tail: EditStep<unknown>[] = [];
	let base: ChainBase | null = null;
	const stale = new Set<string>();
	const left = new Set<string>();
	for (const step of steps) {
		if (step.hidden) continue;
		if (step.tool !== GENERATE_TOOL) {
			tail.push(step);
			continue;
		}
		const params = readGenerateParams(step.params);
		if (!params) continue;
		const result = chosenResult(params);
		if (statusOf(params) !== 'done' || !result) {
			left.add(step.id);
			continue;
		}
		if (original && params.basis && params.basis.geometry !== chainSignature(key, tail)) {
			stale.add(step.id);
			continue;
		}
		base = { step, params, result };
		input = { item_id: result.patch.item_id };
		url = result.url;
		key = refKey(input);
		tail = [];
	}
	return { input, url, base, tail, stale, left };
}

export function basisBeneath(
	steps: readonly EditStep<unknown>[],
	original: MediaToolInputRef | null,
	id: string | null
): { input: MediaToolInputRef | null; tail: EditStep<unknown>[]; basis: GenerateBasis } {
	const at = id ? steps.findIndex((step) => step.id === id) : -1;
	const beneath = at >= 0 ? steps.slice(0, at) : steps.slice();
	const found = rebase(beneath, original);
	return { input: found.input, tail: found.tail, basis: { geometry: chainSignature(refKey(found.input), found.tail), size: null } };
}

export const OUT_OF_DATE_CLIP_LABEL = 'The clip changed';
export const OUT_OF_DATE_CLIP_DETAIL = 'The clip beneath changed';

export function outOfDateNotice(count: number): string | null {
	if (count <= 0) return null;
	return `${count} ${count === 1 ? 'edit is' : 'edits are'} not included: the clip beneath ${count === 1 ? 'it' : 'them'} changed.`;
}
