import type { PromptTabData, GenerationTab } from '$lib/types/tabs';
import { resolvePromptSegments } from '$lib/utils/promptSegments';
import { ensureSegmentList } from '$lib/utils/richSegments';
import { randomUUID } from '$lib/utils/uuid';

export const MAX_BATCH_IMAGES = 20;

export type PromptFields = Pick<
	GenerationTab,
	'prompt' | 'negativePrompt' | 'promptSegments' | 'negativePromptSegments' | 'promptTabs' | 'activePromptTab'
>;

export type PromptListPatch = Pick<
	GenerationTab,
	'prompt' | 'negativePrompt' | 'promptSegments' | 'negativePromptSegments' | 'promptTabs' | 'activePromptTab'
>;

export function canBatchPrompts(source: { supports_prompt_batch?: boolean } | null | undefined): boolean {
	return source?.supports_prompt_batch === true;
}

export function quantityOf(formData: Record<string, unknown> | null | undefined): number {
	const value = Math.floor(Number(formData?.quantity ?? 1));
	return Number.isFinite(value) && value >= 1 ? value : 1;
}

export function emptyPrompt(): PromptTabData {
	return {
		promptSegments: ensureSegmentList([]),
		negativePromptSegments: ensureSegmentList([]),
		prompt: '',
		negativePrompt: '',
		id: randomUUID()
	};
}

export function assignPromptIds(list: PromptTabData[]): PromptTabData[] {
	if (list.every((entry) => entry.id)) return list;
	const taken = new Set(list.map((entry) => entry.id).filter(Boolean));
	let next = 1;
	return list.map((entry) => {
		if (entry.id) return entry;
		while (taken.has(`p${next}`)) next++;
		const id = `p${next}`;
		taken.add(id);
		return { ...entry, id };
	});
}

export function withPromptIds(tab: PromptFields): PromptFields {
	if (promptCount(tab) < 2) return tab;
	return { ...tab, promptTabs: assignPromptIds(tab.promptTabs!) };
}

function workingPrompt(tab: PromptFields): PromptTabData {
	return {
		promptSegments: tab.promptSegments || [],
		negativePromptSegments: tab.negativePromptSegments || [],
		prompt: tab.prompt || '',
		negativePrompt: tab.negativePrompt || ''
	};
}

export function promptCount(tab: Pick<GenerationTab, 'promptTabs'>): number {
	const tabs = tab.promptTabs;
	return tabs && tabs.length > 1 ? tabs.length : 1;
}

export function activePromptIndex(tab: Pick<GenerationTab, 'promptTabs' | 'activePromptTab'>): number {
	const count = promptCount(tab);
	const raw = Math.floor(Number(tab.activePromptTab ?? 0));
	if (!Number.isFinite(raw)) return 0;
	return Math.min(Math.max(raw, 0), count - 1);
}

export function promptList(tab: PromptFields): PromptTabData[] {
	if (promptCount(tab) < 2) return [workingPrompt(tab)];
	return [...tab.promptTabs!];
}

function buildPatch(rawList: PromptTabData[], active: number): PromptListPatch {
	const list = rawList.length < 2 ? rawList : assignPromptIds(rawList);
	if (list.length < 2) {
		const only = list[0] ?? emptyPrompt();
		return {
			promptTabs: undefined,
			activePromptTab: undefined,
			promptSegments: only.promptSegments,
			negativePromptSegments: only.negativePromptSegments,
			prompt: only.prompt,
			negativePrompt: only.negativePrompt
		};
	}
	const index = Math.min(Math.max(active, 0), list.length - 1);
	const current = list[index];
	return {
		promptTabs: list,
		activePromptTab: index,
		promptSegments: current.promptSegments,
		negativePromptSegments: current.negativePromptSegments,
		prompt: current.prompt,
		negativePrompt: current.negativePrompt
	};
}

export function selectPrompt(tab: PromptFields, index: number): PromptListPatch {
	return buildPatch(promptList(tab), index);
}

export function addPrompt(tab: PromptFields): PromptListPatch {
	const list = [...promptList(tab), emptyPrompt()];
	return buildPatch(list, list.length - 1);
}

export function addPromptWith(tab: PromptFields, entry: PromptTabData): PromptListPatch {
	const list = [...promptList(tab), entry];
	return buildPatch(list, list.length - 1);
}

export function insertPrompt(tab: PromptFields, index: number, entry: PromptTabData, active: number): PromptListPatch {
	const list = promptList(tab);
	list.splice(Math.min(Math.max(index, 0), list.length), 0, entry);
	return buildPatch(list, active);
}

export function replacePrompt(tab: PromptFields, index: number, entry: PromptTabData): PromptListPatch | null {
	const list = promptList(tab);
	if (index < 0 || index >= list.length) return null;
	list[index] = entry;
	return buildPatch(list, activePromptIndex(tab));
}

export function duplicatePrompt(tab: PromptFields, index: number): PromptListPatch {
	const list = promptList(tab);
	const source = list[index];
	if (!source) return buildPatch(list, activePromptIndex(tab));
	const copy: PromptTabData = { ...structuredClone(source), id: randomUUID() };
	if (copy.name?.trim()) copy.name = `${copy.name.trim()} copy`;
	else delete copy.name;
	list.splice(index + 1, 0, copy);
	return buildPatch(list, index + 1);
}

export function deletePrompt(tab: PromptFields, index: number): PromptListPatch | null {
	const list = promptList(tab);
	if (list.length < 2 || index < 0 || index >= list.length) return null;
	const active = activePromptIndex(tab);
	list.splice(index, 1);
	const next = index < active ? active - 1 : Math.min(active, list.length - 1);
	return buildPatch(list, next);
}

export function renamePrompt(tab: PromptFields, index: number, name: string): PromptListPatch {
	const list = promptList(tab);
	if (list[index]) {
		const trimmed = name.trim();
		const renamed = { ...list[index] };
		if (trimmed) renamed.name = trimmed;
		else delete renamed.name;
		list[index] = renamed;
	}
	return buildPatch(list, activePromptIndex(tab));
}

const PROMPT_FIELD_KEYS = ['prompt', 'negativePrompt', 'promptSegments', 'negativePromptSegments'] as const;

export function mirrorActivePrompt(next: PromptFields, updates: Partial<PromptFields>): Partial<PromptFields> | null {
	if (!next.promptTabs || next.promptTabs.length < 2) return null;
	if ('promptTabs' in updates || 'activePromptTab' in updates) return null;
	if (!PROMPT_FIELD_KEYS.some((key) => key in updates)) return null;
	const active = activePromptIndex(next);
	const tabs = [...next.promptTabs];
	tabs[active] = { ...tabs[active], ...workingPrompt(next) };
	return { promptTabs: tabs };
}

export function promptTitle(entry: PromptTabData, index: number): string {
	const name = entry.name?.trim();
	if (name) return name;
	const firstLine = (entry.prompt || '')
		.split('\n')
		.map((line) => line.trim())
		.find(Boolean);
	return firstLine || `Prompt ${index + 1}`;
}

export function hasPromptText(entry: PromptTabData): boolean {
	return resolvePromptSegments(entry.promptSegments ?? []).trim().length > 0;
}

export function firstEmptyPrompt(list: PromptTabData[]): number {
	return list.findIndex((entry) => !hasPromptText(entry));
}

export interface BatchSummary {
	prompts: number;
	sent: number;
	perPrompt: number;
	total: number;
	overCap: boolean;
	unsupported: boolean;
	maxPrompts: number;
}

export function batchSummary(input: { prompts: number; perPrompt: number; canBatch: boolean }): BatchSummary {
	const prompts = Math.max(1, Math.floor(input.prompts));
	const perPrompt = Math.max(1, Math.floor(input.perPrompt));
	const unsupported = prompts > 1 && !input.canBatch;
	const sent = unsupported ? 1 : prompts;
	const total = sent * perPrompt;
	return {
		prompts,
		sent,
		perPrompt,
		total,
		overCap: prompts > 1 && !unsupported && total > MAX_BATCH_IMAGES,
		unsupported,
		maxPrompts: Math.max(1, Math.floor(MAX_BATCH_IMAGES / perPrompt))
	};
}

function plural(count: number, word: string): string {
	return `${count} ${word}${count === 1 ? '' : 's'}`;
}

export function batchLine(summary: BatchSummary): { text: string; generateLabel: string; over: boolean } | null {
	if (summary.prompts < 2) return null;
	const lead = summary.unsupported ? `1 of ${summary.prompts} prompts` : plural(summary.prompts, 'prompt');
	return {
		text: `${lead} × ${plural(summary.perPrompt, 'image')} = ${plural(summary.total, 'image')}`,
		generateLabel: `Generate ${plural(summary.total, 'image')}`,
		over: summary.overCap
	};
}

export const BATCH_CAP_MESSAGE = `Up to ${MAX_BATCH_IMAGES} images per run.`;

export const LOCKED_BATCH_COPY = 'This preset runs one prompt at a time.';

export function imageWord(perPrompt: number): string {
	return perPrompt === 1 ? 'image' : 'images';
}

export interface AddPromptState {
	atLimit: boolean;
	disabled: boolean;
	explanation: string | null;
}

export function addPromptState(input: {
	canBatch: boolean;
	count: number;
	maxPrompts: number;
	perPrompt: number;
}): AddPromptState {
	const atLimit = input.canBatch && input.count >= input.maxPrompts;
	let explanation: string | null = null;
	if (!input.canBatch) explanation = LOCKED_BATCH_COPY;
	else if (atLimit) {
		explanation = `A run can make up to ${MAX_BATCH_IMAGES} images. At ${input.perPrompt} ${imageWord(input.perPrompt)} each, that is ${input.maxPrompts} prompts.`;
	}
	return { atLimit, disabled: !input.canBatch || atLimit, explanation };
}

export function batchPromptFields(
	entries: Array<{ prompt_index?: unknown; prompt?: unknown } | null | undefined>,
	index: number
): { promptIndex: number; promptText?: string; promptImage: number } | null {
	const promptIndex = entries[index]?.prompt_index;
	if (typeof promptIndex !== 'number' || !Number.isInteger(promptIndex) || promptIndex < 0) return null;
	let promptImage = 1;
	for (let i = 0; i < index; i++) {
		if (entries[i]?.prompt_index === promptIndex) promptImage += 1;
	}
	const text = entries[index]?.prompt;
	return { promptIndex, ...(typeof text === 'string' ? { promptText: text } : {}), promptImage };
}

export function batchTileLabel(batch: { promptIndex: number; promptImage: number } | null): string | null {
	return batch ? `${batch.promptIndex + 1}.${batch.promptImage}` : null;
}
