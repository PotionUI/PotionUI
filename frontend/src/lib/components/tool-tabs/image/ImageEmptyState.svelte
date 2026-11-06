<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Kbd, Spinner } from '$lib/components/ui';
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
			{#each sourceEntries('image', phone) as entry (entry.id)}
				<Button size="sm" icon={entry.icon} onclick={() => picker?.open(entry.id)}>{entry.label}</Button>
			{/each}
		</div>
		{#if !phone}
			<p class="mt-4 flex items-center gap-2 text-sm text-fg-subtle">or paste <Kbd keys="Ctrl V" /></p>
		{/if}
		{#if error}
			<p class="mt-3 text-sm text-danger" role="alert">{error}</p>
		{/if}
		<RecentPictures {onpick} class="mt-6 w-full border-t border-line pt-5 text-left" />
	</div>
</div>

<ImageSourcePicker bind:this={picker} {onpick} {onfile} />
