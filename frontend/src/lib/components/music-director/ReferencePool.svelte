<script lang="ts">
	// The Music Director document's inline reference-audio pool (see
	// docs/music-director.md `references`) -- unlike Video Director's
	// whole-form pool, this one lives entirely on the document itself, so
	// adding a track is a plain upload, never a "from form" pick.
	import type { MusicReferenceItem } from '$lib/types/musicDirector';
	import MediaLoaderField from '$lib/components/form-fields/MediaLoaderField.svelte';
	import Icon from '$lib/components/Icon.svelte';

	let {
		references,
		onAdd,
		onRemove,
		maxReferenceSeconds = null,
		label = 'Style reference'
	}: {
		references: MusicReferenceItem[];
		onAdd: (media: import('$lib/types/tabs').MediaRef) => void;
		onRemove: (id: string) => void;
		maxReferenceSeconds?: number | null;
		label?: string;
	} = $props();

	// A single "add" slot: MediaLoaderField always mints its own value; once one
	// arrives we hand it to `onAdd` (which mints the pool entry's id) and reset
	// this slot back to empty so it's ready for the next upload.
	let pendingValue: unknown = $state(null);

	function handlePendingChange(_name: string, value: unknown) {
		if (value && typeof value === 'object' && typeof (value as { path?: unknown }).path === 'string') {
			onAdd(value as import('$lib/types/tabs').MediaRef);
		}
		pendingValue = null;
	}
</script>

<div class="rounded-lg border border-line bg-canvas p-3 shadow-[inset_0_1px_2px_rgb(0_0_0_/_0.35)]">
	<div class="mb-2 flex items-center gap-2">
		<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">{label}</span>
		{#if maxReferenceSeconds != null}
			<span class="font-mono text-2xs text-fg-subtle">max {maxReferenceSeconds}s</span>
		{/if}
	</div>
	<div class="flex flex-wrap items-center gap-2">
		{#each references as ref (ref.id)}
			<span class="inline-flex items-center gap-1.5 rounded border border-line-strong bg-surface-2 px-2 py-1 font-mono text-2xs text-fg-muted">
				<Icon name="audio" className="h-3 w-3 flex-shrink-0" />
				<span class="max-w-[140px] truncate">{ref.media.label || ref.media.name || ref.media.path.split('/').pop()}</span>
				<button
					type="button"
					class="ml-0.5 text-fg-subtle hover:text-danger"
					onclick={() => onRemove(ref.id)}
					aria-label="Remove reference"
				>
					<Icon name="x" className="h-3 w-3" />
				</button>
			</span>
		{/each}
		<MediaLoaderField
			name="music_director_reference"
			value={pendingValue}
			onChange={handlePendingChange}
			config={{ accept: ['audio'] }}
			compact
		/>
	</div>
</div>
