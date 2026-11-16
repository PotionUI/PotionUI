<script lang="ts">
	import { Button, EmptyState, Kbd, Spinner } from '$lib/components/ui';
	import ImageSourcePicker from './ImageSourcePicker.svelte';
	import RecentPictures from './RecentPictures.svelte';
	import { sourceEntries } from '$lib/tool-tabs/sourceEntries';
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

	let picker = $state<ReturnType<typeof ImageSourcePicker>>();
	let dragging = $state(false);

	function drop(event: DragEvent) {
		event.preventDefault();
		dragging = false;
		const file = Array.from(event.dataTransfer?.files ?? []).find((candidate) => candidate.type.startsWith('image/'));
		if (file) onfile(file);
	}
</script>

<div
	class="relative flex min-h-0 flex-1 flex-col overflow-y-auto dot-grid bg-canvas {dragging ? 'bg-signal/5 ring-1 ring-inset ring-signal' : ''}"
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
	<div class="flex flex-1 flex-col" data-testid="tool-tab-empty">
		<EmptyState
			page
			icon="upload"
			title="Open a picture to edit"
			description="Drop an image here, paste one, or pick something you made. Every edit stays undoable until you save."
		>
			{#snippet extra()}
				{#if busy}
					<span class="mt-1 flex justify-center"><Spinner /></span>
				{/if}
				{#if !phone}
					<span class="flex items-center justify-center gap-2">or paste <Kbd keys="Ctrl V" /></span>
				{/if}
				{#if error}
					<span class="block text-danger" role="alert">{error}</span>
				{/if}
			{/snippet}
			{#snippet actions()}
				{#each sourceEntries('image', phone) as entry (entry.id)}
					<Button variant="secondary" icon={entry.icon} onclick={() => picker?.open(entry.id)}>{entry.label}</Button>
				{/each}
			{/snippet}
		</EmptyState>
	</div>
	<RecentPictures {onpick} class="mx-auto w-full max-w-md px-6 pb-10 text-left" />
</div>

<ImageSourcePicker bind:this={picker} {onpick} {onfile} />
