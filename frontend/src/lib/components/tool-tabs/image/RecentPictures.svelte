<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api/index';
	import { recentHistoryPictures } from '$lib/tool-tabs/image/sourceRefs';
	import type { MediaRef } from '$lib/types/tabs';
	import JustifiedTiles from './JustifiedTiles.svelte';

	let {
		onpick,
		columns: _columns = '',
		class: className = ''
	}: {
		onpick: (source: MediaRef) => void;
		columns?: string;
		class?: string;
	} = $props();

	let recent = $state<MediaRef[]>([]);
	let items = $derived(
		recent.map((ref) => ({ key: ref.path, ref, aspect: ref.width && ref.height ? ref.width / ref.height : null }))
	);

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
		<JustifiedTiles {items} gap={8} rowHeight={88} data-recent-history>
			{#snippet tile(item, box, report)}
				<button
					type="button"
					class="block overflow-hidden rounded bg-surface-3 outline-none ring-signal hover:ring-1 focus-visible:ring-1"
					style="width: {box.width}px; height: {box.height}px; max-width: 100%"
					aria-label="Edit {item.ref.name}"
					onclick={() => onpick(item.ref)}
				>
					<img
						src={item.ref.url}
						alt=""
						class="h-full w-full object-contain"
						loading="lazy"
						onload={(event) => {
							const img = event.currentTarget as HTMLImageElement;
							report(img.naturalWidth, img.naturalHeight);
						}}
					/>
				</button>
			{/snippet}
		</JustifiedTiles>
	</div>
{/if}
