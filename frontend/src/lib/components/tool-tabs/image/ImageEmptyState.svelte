<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import GenerationHistoryModal from '$lib/components/modals/GenerationHistoryModal.svelte';
	import UploadLibraryModal from '$lib/components/modals/UploadLibraryModal.svelte';
	import { Button, Kbd, Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import type { LibraryItem } from '$lib/services/api/library';
	import { historyFileRef, libraryItemRef, recentHistoryPictures } from '$lib/tool-tabs/image/sourceRefs';
	import type { GenerationFile } from '$lib/types/history';
	import type { MediaRef } from '$lib/types/tabs';

	let {
		phone = false,
		busy = false,
		error = null,
		onpick,
		onfile
	}: {
		phone?: boolean;
		busy?: boolean;
		error?: string | null;
		onpick: (source: MediaRef) => void;
		onfile: (file: File) => void;
	} = $props();

	let showHistory = $state(false);
	let showLibrary = $state(false);
	let recent = $state<MediaRef[]>([]);
	let input = $state<HTMLInputElement>();
	let dragging = $state(false);

	onMount(async () => {
		try {
			const response = await api.getGenerationHistory({ limit: 12, mediaType: 'image', status: 'completed' });
			if (response.success && response.data) recent = recentHistoryPictures(response.data.generations);
		} catch {
			recent = [];
		}
	});

	function fromHistory(_generation: unknown, file: GenerationFile) {
		showHistory = false;
		if (file?.file_path) onpick(historyFileRef(file));
	}

	function fromLibrary(item: LibraryItem) {
		showLibrary = false;
		onpick(libraryItemRef(item));
	}

	function browse(event: Event) {
		const target = event.currentTarget as HTMLInputElement;
		const file = target.files?.[0];
		target.value = '';
		if (file) onfile(file);
	}

	function drop(event: DragEvent) {
		event.preventDefault();
		dragging = false;
		const file = Array.from(event.dataTransfer?.files ?? []).find((candidate) => candidate.type.startsWith('image/'));
		if (file) onfile(file);
	}
</script>

<div
	class="dot-grid relative flex min-h-0 flex-1 items-center justify-center overflow-y-auto p-4 md:p-6"
	role="region"
	aria-label="Open a picture"
	ondragover={(event) => {
		event.preventDefault();
		dragging = true;
	}}
	ondragleave={() => (dragging = false)}
	ondrop={drop}
	data-image-empty
>
	<div
		class="flex w-full max-w-[560px] flex-col items-center rounded-lg border border-dashed bg-surface-1 px-5 py-8 text-center md:px-8 {dragging
			? 'border-signal bg-signal/5'
			: 'border-line-strong'}"
		data-testid="tool-tab-empty"
	>
		<span class="mb-4 flex h-11 w-11 items-center justify-center rounded border border-line bg-surface-2 text-fg-muted">
			{#if busy}<Spinner />{:else}<Icon name="upload" className="h-5 w-5" />{/if}
		</span>
		<h2 class="text-lg font-semibold text-fg">Open a picture to edit</h2>
		<p class="mt-2 max-w-sm text-base text-fg-muted">
			Drop an image here, paste one, or pick something you made. Every edit stays undoable until you save.
		</p>
		<div class="mt-5 flex flex-wrap justify-center gap-2">
			<Button size="sm" icon="image" onclick={() => (showLibrary = true)}>Library</Button>
			<Button size="sm" icon="clock" onclick={() => (showHistory = true)}>History</Button>
			<Button size="sm" icon="folder" onclick={() => input?.click()}>{phone ? 'Photos' : 'Browse files'}</Button>
			<input bind:this={input} type="file" accept="image/*" class="hidden" onchange={browse} data-image-browse />
		</div>
		{#if !phone}
			<p class="mt-4 flex items-center gap-2 text-sm text-fg-subtle">or paste <Kbd keys="Ctrl V" /></p>
		{/if}
		{#if error}
			<p class="mt-3 text-sm text-danger" role="alert">{error}</p>
		{/if}
		{#if recent.length}
			<div class="mt-6 w-full border-t border-line pt-5 text-left">
				<p class="mb-2.5 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Recent in History</p>
				<div class="grid grid-cols-3 gap-2 md:grid-cols-6" data-recent-history>
					{#each recent as ref (ref.path)}
						<button
							type="button"
							class="aspect-square overflow-hidden rounded bg-surface-3 outline-none ring-signal hover:ring-1 focus-visible:ring-1"
							aria-label="Edit {ref.name}"
							onclick={() => onpick(ref)}
						>
							<img src={ref.url} alt="" class="h-full w-full object-cover" loading="lazy" />
						</button>
					{/each}
				</div>
			</div>
		{/if}
	</div>
</div>

<GenerationHistoryModal isOpen={showHistory} onClose={() => (showHistory = false)} onSelect={fromHistory} mediaType="image" title="Select an image from History" />
<UploadLibraryModal isOpen={showLibrary} onClose={() => (showLibrary = false)} onSelect={fromLibrary} mediaType="image" title="Select an image from your Library" />
