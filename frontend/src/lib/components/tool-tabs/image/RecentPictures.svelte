<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { recentHistoryPictures } from '$lib/tool-tabs/image/sourceRefs';
	import type { MediaRef } from '$lib/types/tabs';

	let {
		onpick,
		columns = 'grid-cols-3 md:grid-cols-6',
		class: className = ''
	}: {
		onpick: (source: MediaRef) => void;
		columns?: string;
		class?: string;
	} = $props();

	let recent = $state<MediaRef[]>([]);

	onMount(async () => {
		try {
			const response = await api.getGenerationHistory({ limit: 12, mediaType: 'image', status: 'completed' });
			if (response.success && response.data) recent = recentHistoryPictures(response.data.generations);
		} catch {
			recent = [];
		}
	});
</script>

{#if recent.length}
	<div class={className}>
		<p class="mb-2.5 font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Recent in History</p>
		<div class="grid gap-2 {columns}" data-recent-history>
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
