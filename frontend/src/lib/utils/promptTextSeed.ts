import type { PromptTabData, GenerationTab } from '$lib/types/tabs';
import type { Segment } from '$lib/types/segments';
import { isPristinePlaceholderList, toEditorSegment } from '$lib/utils/richSegments';

type PromptFields = Pick<GenerationTab, 'prompt' | 'negativePrompt' | 'promptSegments' | 'negativePromptSegments'>;

function seedSide(text: string | undefined, segments: Segment[] | undefined, id: string): Segment[] | null {
	const list = segments ?? [];
	if (!(text ?? '').trim()) return null;
	if (list.length > 0 && !isPristinePlaceholderList(list)) return null;
	return [toEditorSegment({ type: 'content', content: text as string, chips: {}, enabled: true }, () => id)];
}

export function seedSegmentsFromText<T extends Partial<PromptFields>>(fields: T): T {
	const positive = seedSide(fields.prompt, fields.promptSegments, 'seeded-positive');
	const negative = seedSide(fields.negativePrompt, fields.negativePromptSegments, 'seeded-negative');
	if (!positive && !negative) return fields;
	return {
		...fields,
		...(positive ? { promptSegments: positive } : {}),
		...(negative ? { negativePromptSegments: negative } : {})
	};
}

export function seedPromptTabs(tabs: PromptTabData[] | undefined): PromptTabData[] | undefined {
	if (!tabs) return tabs;
	let changed = false;
	const next = tabs.map((entry) => {
		const seeded = seedSegmentsFromText(entry);
		if (seeded !== entry) changed = true;
		return seeded;
	});
	return changed ? next : tabs;
}
