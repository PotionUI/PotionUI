import { isTaken, type AcceptedKinds } from './kitCounts';
import type { KitDirectory } from './kitChips';
import { parseKitKey } from './kitMarker';
import type { KitDetail, KitItem } from './types';
import {
	findResourceSpec,
	mediaFieldItems,
	renderResourceToken,
	resourceMarkerRegex,
	type PromptResourceKind,
	type PromptResourceSpec
} from '$lib/utils/promptResources';
import { kindOfMediaItem } from '$lib/components/form-fields/mediaLoaderKind';

export function expandKitCitations(
	text: string,
	specs: readonly PromptResourceSpec[],
	values: Record<string, unknown>,
	directory: KitDirectory
): string {
	if (!text || !text.includes('@[') || !text.includes('kit:')) return text;
	const taken = new Map<string, number>();
	const counters = new Map<string, number>();
	const accepted = (field: string): AcceptedKinds => {
		const kinds = specs.filter((spec) => spec.field === field && !spec.source).map((spec) => spec.kind);
		return kinds.length > 0 ? new Set<KitItem['kind']>(['text', ...kinds]) : null;
	};

	const tokenFor = (field: string, kit: KitDetail, item: KitItem): string => {
		const kind = item.kind as PromptResourceKind;
		const spec = findResourceSpec(specs, field, kind);
		if (!spec) return '';
		const slot = `${field}\u0000${kind}`;
		const known = taken.get(`${kit.id}/${item.id}\u0000${field}`);
		if (known !== undefined) return renderResourceToken(spec, known);
		const existing = counters.get(slot) ?? mediaFieldItems(values[field]).filter((entry) => kindOfMediaItem(entry) === kind).length;
		const position = existing + 1;
		counters.set(slot, position);
		taken.set(`${kit.id}/${item.id}\u0000${field}`, position);
		return renderResourceToken(spec, position);
	};

	const cite = (field: string, kit: KitDetail, item: KitItem): string => {
		if (item.kind === 'text') return item.text ?? '';
		if (item.missing || !isTaken(item.kind, accepted(field))) return '';
		return tokenFor(field, kit, item);
	};

	return text.replace(resourceMarkerRegex(), (full, field: string, itemKey: string) => {
		const key = parseKitKey(itemKey);
		if (!key) return full;
		const kit = directory.get(key.kitId);
		if (!kit) return '';
		if (key.itemId) {
			const item = kit.items.find((candidate) => candidate.id === key.itemId);
			return item ? cite(field, kit, item) : '';
		}
		const media = kit.items.filter((item) => item.kind !== 'text').map((item) => cite(field, kit, item));
		const notes = kit.items.filter((item) => item.kind === 'text').map((item) => cite(field, kit, item));
		return [...media, ...notes].filter(Boolean).join(' ');
	});
}
