import { writable, type Readable } from 'svelte/store';
import type { APIResponse } from '$lib/types/api';
import type { KitNotice, KitResolution, KitResolveRequest } from './types';

const MAX_TEXT = 20000;
const MAX_LOADED = 50;

export function hasKitCites(texts: readonly (string | null | undefined)[]): boolean {
	return texts.some((text) => typeof text === 'string' && (text.includes('@kits.') || /@\[[A-Za-z_][\w-]*:kit:/.test(text)));
}

export function plainLine(message: string): string {
	return message.replace(/`/g, '');
}

export function skipLines(notices: readonly KitNotice[]): string[] {
	return [...new Set(notices.map((notice) => plainLine(notice.message)))];
}

export interface SkipSummary {
	count: number;
	title: string;
	detail: string;
}

export function summarizeSkips(notices: readonly KitNotice[]): SkipSummary | null {
	const items = notices.filter((notice) => notice.item);
	const count = items.length > 0 ? new Set(items.map((notice) => `${notice.kit}/${notice.item}`)).size : notices.length;
	if (count === 0) return null;
	const names = [...new Set(items.map((notice) => notice.item as string))];
	const kinds = [...new Set(items.map((notice) => notice.kind).filter((kind): kind is string => !!kind))];
	const joined = names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}` : (names[0] ?? '');
	const detail = names.length > 0 ? (kinds.length > 0 ? `${joined}: ${kinds.join(', ')}` : joined) : plainLine(notices[0].message);
	return { count, title: `${count} ${count === 1 ? 'item' : 'items'} skipped`, detail };
}

export function buildResolveRequest(input: {
	texts: readonly (string | null | undefined)[];
	loaded: readonly string[];
	field: string | null;
	presetId: string | null | undefined;
	mode: string | null | undefined;
}): KitResolveRequest | null {
	if (!hasKitCites(input.texts)) return null;
	return {
		text: input.texts.filter((text): text is string => !!text).join('\n').slice(0, MAX_TEXT),
		loaded: input.loaded.slice(0, MAX_LOADED),
		field: input.field,
		preset_id: input.presetId ?? null,
		mode: input.mode ?? null
	};
}

export interface ResolverState {
	loading: boolean;
	failed: boolean;
	result: KitResolution | null;
}

export interface KitResolver extends Readable<ResolverState> {
	request(request: KitResolveRequest | null): void;
	cancel(): void;
}

export function createKitResolver(
	call: (request: KitResolveRequest) => Promise<APIResponse<KitResolution>>,
	delay = 400
): KitResolver {
	const store = writable<ResolverState>({ loading: false, failed: false, result: null });
	let timer: ReturnType<typeof setTimeout> | undefined;
	let sequence = 0;
	let lastKey: string | null = null;

	function cancel() {
		clearTimeout(timer);
		timer = undefined;
		sequence += 1;
	}

	function request(next: KitResolveRequest | null) {
		if (next === null) {
			cancel();
			lastKey = null;
			store.set({ loading: false, failed: false, result: null });
			return;
		}
		const key = JSON.stringify(next);
		if (key === lastKey) return;
		lastKey = key;
		clearTimeout(timer);
		const mine = ++sequence;
		store.update((state) => ({ ...state, loading: true }));
		timer = setTimeout(async () => {
			try {
				const response = await call(next);
				if (mine !== sequence) return;
				if (response.success && response.data) store.set({ loading: false, failed: false, result: response.data });
				else store.update((state) => ({ loading: false, failed: true, result: state.result }));
			} catch {
				if (mine === sequence) store.update((state) => ({ loading: false, failed: true, result: state.result }));
			}
		}, delay);
	}

	return { subscribe: store.subscribe, request, cancel };
}
