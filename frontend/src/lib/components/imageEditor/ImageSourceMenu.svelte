<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import GenerationHistoryModal from '$lib/components/modals/GenerationHistoryModal.svelte';
	import UploadLibraryModal from '$lib/components/modals/UploadLibraryModal.svelte';
	import { Spinner } from '$lib/components/ui';
	import { PAINT_ICONS } from './icons';
	import { canvasFromBlob, canvasFromUrl, displayName, generationImageUrl } from './loadPicked';
	import { imageSources } from './registries';
	import type { ImageSource, PickedImage } from './types';

	export let mode: 'open' | 'layer';
	export let dirty: boolean = false;
	export let onPick: (image: PickedImage) => void;
	export let onClose: () => void;

	let showHistory = false;
	let showLibrary = false;
	let busy = false;
	let failure: string | null = null;
	let fileInput: HTMLInputElement;
	let sources: ImageSource[] = imageSources.list();
	const stopSources = imageSources.subscribe(() => (sources = imageSources.list()));

	const row =
		'flex items-center gap-2 w-full px-2.5 h-9 max-md:h-11 rounded text-left text-sm text-fg-muted transition-colors hover:bg-surface-3/50 hover:text-fg disabled:opacity-50';

	async function finish(task: Promise<PickedImage | null>) {
		busy = true;
		failure = null;
		try {
			const picked = await task;
			if (picked) {
				onPick(picked);
				onClose();
			}
		} catch {
			failure = 'That image could not be loaded.';
		} finally {
			busy = false;
		}
	}

	function fromHistory(_generation: unknown, file: { file_path?: string } | null) {
		showHistory = false;
		const url = file?.file_path ? generationImageUrl(file.file_path) : null;
		if (!url || !file?.file_path) return;
		const name = file.file_path.split('/').pop() ?? 'image.png';
		finish(canvasFromUrl(url).then((canvas) => ({ canvas, name })));
	}

	function fromLibrary(item: { url: string; original_filename?: string; filename: string }) {
		showLibrary = false;
		finish(
			canvasFromUrl(item.url).then((canvas) => ({
				canvas,
				name: displayName([item.original_filename, item.filename], 'image.png')
			}))
		);
	}

	function fromFile(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		if (!file) return;
		finish(canvasFromBlob(file).then((canvas) => ({ canvas, name: file.name })));
	}

	async function readClipboard(): Promise<PickedImage | null> {
		const items = await navigator.clipboard.read();
		for (const item of items) {
			const type = item.types.find((candidate) => candidate.startsWith('image/'));
			if (type) {
				const blob = await item.getType(type);
				return { canvas: await canvasFromBlob(blob), name: 'pasted-image.png' };
			}
		}
		failure = 'There is no image on the clipboard.';
		return null;
	}

	function fromClipboard() {
		finish(
			readClipboard().catch(() => {
				failure = 'The clipboard is not available here. Press Ctrl+V instead.';
				return null;
			})
		);
	}

	function onPaste(event: ClipboardEvent) {
		const file = [...(event.clipboardData?.files ?? [])].find((candidate) =>
			candidate.type.startsWith('image/')
		);
		if (!file) return;
		event.preventDefault();
		finish(canvasFromBlob(file).then((canvas) => ({ canvas, name: 'pasted-image.png' })));
	}

	function fromPlugin(source: ImageSource) {
		finish(
			source.pick().then(async (result) => {
				if (!result) return null;
				if (result.canvas) return { canvas: result.canvas, name: result.name };
				if (result.blob) return { canvas: await canvasFromBlob(result.blob), name: result.name };
				if (result.url) return { canvas: await canvasFromUrl(result.url), name: result.name };
				return null;
			})
		);
	}

	function onWindowClick(event: MouseEvent) {
		const target = event.target as HTMLElement | null;
		if (showHistory || showLibrary) return;
		if (!target?.closest('[data-image-source-menu]') && !target?.closest('[data-image-source-trigger]')) {
			onClose();
		}
	}

	onMount(() => {
		window.addEventListener('paste', onPaste);
	});

	onDestroy(() => {
		window.removeEventListener('paste', onPaste);
		stopSources();
	});
</script>

<svelte:window on:click={onWindowClick} />

<div
	data-image-source-menu
	role="menu"
	aria-label={mode === 'open' ? 'Open an image' : 'Add an image as a layer'}
	class="absolute left-2 top-full mt-1 z-20 w-72 max-w-[calc(100vw-1rem)] rounded-xl border border-line-strong bg-surface-1 shadow-floating p-1.5 flex flex-col gap-0.5"
>
	<p class="px-2.5 pt-1.5 pb-1 font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">
		{mode === 'open' ? 'Open image' : 'Add image as layer'}
	</p>

	{#if mode === 'open' && dirty}
		<p class="mx-1 mb-1 rounded border border-warning/40 px-2 py-1.5 text-xs text-warning">
			This replaces the drawing in the editor. Your unsaved changes will be lost.
		</p>
	{/if}

	<button type="button" role="menuitem" class={row} disabled={busy} on:click={() => (showHistory = true)}>
		<svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
		History
	</button>
	<button type="button" role="menuitem" class={row} disabled={busy} on:click={() => (showLibrary = true)}>
		<svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M4 6h16M4 10h16M4 14h16M4 18h16" /></svg>
		Library
	</button>
	<button type="button" role="menuitem" class={row} disabled={busy} on:click={() => fileInput.click()}>
		<svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.open} /></svg>
		Browse files
	</button>
	<button type="button" role="menuitem" class={row} disabled={busy} on:click={fromClipboard}>
		<svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.duplicate} /></svg>
		Paste from clipboard
	</button>
	{#each sources as source (source.id)}
		<button type="button" role="menuitem" class={row} disabled={busy} on:click={() => fromPlugin(source)}>
			<svg class="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={PAINT_ICONS.addImage} /></svg>
			{source.label}
		</button>
	{/each}

	{#if busy}
		<p class="inline-flex items-center gap-2 px-2.5 py-1.5 text-xs text-fg-muted">
			<Spinner size="sm" />
			Loading image
		</p>
	{/if}
	{#if failure}
		<p class="px-2.5 py-1.5 text-xs text-danger">{failure}</p>
	{/if}

	<input bind:this={fileInput} type="file" accept="image/*" class="hidden" on:change={fromFile} />
</div>

<GenerationHistoryModal
	isOpen={showHistory}
	onClose={() => (showHistory = false)}
	onSelect={fromHistory}
	mediaType="image"
	title="Select an image from History"
/>
<UploadLibraryModal
	isOpen={showLibrary}
	onClose={() => (showLibrary = false)}
	onSelect={fromLibrary}
	mediaType="image"
	title="Select an image from your Library"
/>
