import { isKitKey, parseKitKey } from '$lib/kits/kitMarker';
import { kindOfMediaItem } from '$lib/components/form-fields/mediaLoaderKind';
import {
	itemPosition,
	kindLabel,
	mediaFieldItems,
	mediaItemKey,
	resourceMarkerRegex,
	type PromptResourceKind,
	type PromptResourceSpec
} from '$lib/utils/promptResources';

export type RefPart =
	| { type: 'text'; text: string }
	| { type: 'ref'; raw: string; field: string; key: string };

export type RefView =
	| { type: 'kit'; field: string; key: string }
	| {
			type: 'media';
			field: string;
			key: string;
			spec: PromptResourceSpec;
			position: number;
			item: { url: string | null; name: string };
	  }
	| { type: 'missing'; field: string; key: string };

export function hasRefTokens(text: unknown): boolean {
	return typeof text === 'string' && text.includes('@[') && resourceMarkerRegex().test(text);
}

export function tokenizeRefs(text: string): RefPart[] {
	if (!hasRefTokens(text)) return [{ type: 'text', text }];
	const parts: RefPart[] = [];
	let cursor = 0;
	for (const match of text.matchAll(resourceMarkerRegex())) {
		const start = match.index ?? 0;
		if (start > cursor) parts.push({ type: 'text', text: text.slice(cursor, start) });
		parts.push({ type: 'ref', raw: match[0], field: match[1], key: match[2] });
		cursor = start + match[0].length;
	}
	if (cursor < text.length) parts.push({ type: 'text', text: text.slice(cursor) });
	return parts;
}

const KIND_BY_EXTENSION: Record<string, PromptResourceKind> = {
	png: 'image',
	jpg: 'image',
	jpeg: 'image',
	webp: 'image',
	gif: 'image',
	bmp: 'image',
	avif: 'image',
	mp4: 'video',
	webm: 'video',
	mov: 'video',
	mkv: 'video',
	mp3: 'audio',
	wav: 'audio',
	flac: 'audio',
	ogg: 'audio',
	m4a: 'audio'
};

export function kindFromKey(key: string): PromptResourceKind | null {
	const extension = key.split('?')[0].split('.').pop()?.toLowerCase() ?? '';
	return KIND_BY_EXTENSION[extension] ?? null;
}

function baseName(key: string): string {
	return key.split('/').pop() || key;
}

export function uploadUrl(key: string): string | null {
	return key.startsWith('uploads/') ? `/api/media/uploads/${baseName(key)}` : null;
}

export function describeRefs(
	parts: readonly RefPart[],
	formData: Record<string, unknown> | null | undefined
): RefView[] {
	const appearance = new Map<string, Map<string, number>>();
	const views: RefView[] = [];
	for (const part of parts) {
		if (part.type !== 'ref') continue;
		const { field, key } = part;
		if (isKitKey(key) && parseKitKey(key)) {
			views.push({ type: 'kit', field, key });
			continue;
		}
		const value = formData?.[field];
		const item = mediaFieldItems(value).find((candidate) => mediaItemKey(candidate) === key);
		const kind = (item !== undefined ? kindOfMediaItem(item) : null) ?? kindFromKey(key);
		if (!kind) {
			views.push({ type: 'missing', field, key });
			continue;
		}
		let position = item !== undefined ? itemPosition(value, key, kind) : null;
		if (position === null) {
			const group = `${field}:${kind}`;
			const order = appearance.get(group) ?? new Map<string, number>();
			if (!order.has(key)) order.set(key, order.size + 1);
			appearance.set(group, order);
			position = order.get(key) as number;
		}
		const record = item && typeof item === 'object' ? (item as Record<string, unknown>) : {};
		const url = typeof record.url === 'string' && record.url ? record.url : uploadUrl(key);
		const name = typeof record.name === 'string' && record.name ? record.name : baseName(key);
		views.push({
			type: 'media',
			field,
			key,
			spec: { field, kind, token: `<${kindLabel(kind)} @>` },
			position,
			item: { url, name }
		});
	}
	return views;
}
