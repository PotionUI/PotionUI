<script lang="ts">
	import { Button, EmptyState } from '$lib/components/ui';
	import { DetailBody, DetailHeader, DetailSection } from '$lib/components/detail';
	import { isMobile } from '$lib/stores/viewport';
	import PathText from './PathText.svelte';
	import type { FoldersController } from './controller.svelte';
	import { formatCount, typeLabel } from './derive';

	let { ctrl, onBack }: { ctrl: FoldersController; onBack?: () => void } = $props();

	const unplaced = $derived(ctrl.overview?.unplaced ?? []);
</script>

<DetailHeader title="Found outside your folders" icon="inbox" backLabel={$isMobile ? 'Models' : undefined} {onBack}>
	{#snippet subtitle()}<span class="font-sans text-[12px]">Models were found in these places, but they are not part of a folder yet.</span>{/snippet}
</DetailHeader>
<DetailBody>
	<DetailSection label="Suggested folders" padded={false}>
		{#each unplaced as u (u.dir)}
			<div class="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-4 py-3 first:border-t-0 sm:px-5" data-suggested-row>
				<div class="min-w-0 flex-1 basis-60">
					<PathText text={u.dir} muted={false} />
					<span class="text-[12px] text-fg-subtle">{formatCount(u.count)} file{u.count === 1 ? '' : 's'} - {u.types.map(typeLabel).join(', ')}</span>
				</div>
				<Button variant="secondary" size="sm" onclick={() => ctrl.openAdd(u.dir)}>Add as folder</Button>
			</div>
		{/each}
		{#if unplaced.length === 0}<EmptyState compact icon="folder" title="Nothing found" description="Nothing found outside your folders." />{/if}
	</DetailSection>
</DetailBody>
