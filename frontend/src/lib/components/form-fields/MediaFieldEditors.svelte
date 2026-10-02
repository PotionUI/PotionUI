<script lang="ts">
	import MediaEditors from '$lib/media/editors/MediaEditors.svelte';
	import {
		hasEditor,
		type MediaEditorKind,
		type MediaEditorRequest,
		type MediaEditorResult
	} from '$lib/media/editors';
	import NewDrawingModal from '$lib/components/imageEditor/NewDrawingModal.svelte';
	import { readDrawConfig, type DrawConfig } from '$lib/components/imageEditor/drawConfig';
	import { kindOfMediaItem } from './mediaLoaderKind';
	import type { MediaItemMetadata } from './mediaLoaderMeta';

	let {
		config,
		existingMaskUrl,
		onOpenEditor,
		onResult
	}: {
		config: unknown;
		existingMaskUrl: string | null;
		onOpenEditor?: (request: MediaEditorRequest) => void;
		onResult: (result: MediaEditorResult, request: MediaEditorRequest) => void;
	} = $props();

	let request = $state<MediaEditorRequest | null>(null);
	let drawing = $state(false);
	let drawDefaults = $derived(readDrawConfig(config));

	function dispatch(next: MediaEditorRequest) {
		if (onOpenEditor) onOpenEditor(next);
		else request = next;
	}

	export function open(
		kind: MediaEditorKind,
		item: unknown,
		index: number | null,
		fallback: { url: string; name: string }
	) {
		const mediaKind = kindOfMediaItem(item);
		if (!mediaKind || !hasEditor(kind, mediaKind)) return;

		const record = (item && typeof item === 'object' ? item : {}) as Record<string, unknown>;
		const meta = (record.metadata && typeof record.metadata === 'object' ? record.metadata : null) as MediaItemMetadata | null;
		dispatch({
			kind,
			source: {
				url: (record.url as string) || fallback.url,
				kind: mediaKind,
				fileName: (record.name as string) || fallback.name,
				storedPath: (record.relative_path as string) || (record.path as string) || null,
				width: meta?.width ?? null,
				height: meta?.height ?? null,
				durationSeconds: meta?.duration_seconds ?? null,
				fps: meta?.fps ?? null
			},
			itemIndex: index
		});
	}

	export function draw() {
		drawing = true;
	}

	function startDrawing(settings: DrawConfig) {
		drawing = false;
		dispatch({
			kind: 'paint',
			source: { url: '', kind: 'image', fileName: 'Untitled drawing' },
			itemIndex: null,
			draw: settings
		});
	}
</script>

<NewDrawingModal
	isOpen={drawing}
	presetSize={drawDefaults.size}
	defaultBackground={drawDefaults.background ?? 'white'}
	defaultPen={drawDefaults.pen}
	onCreate={startDrawing}
	onClose={() => (drawing = false)}
/>

<MediaEditors {request} {existingMaskUrl} onClose={() => (request = null)} {onResult} />
