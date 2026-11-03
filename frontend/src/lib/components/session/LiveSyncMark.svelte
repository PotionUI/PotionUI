<script lang="ts">
	import type { Snippet } from 'svelte';
	import { sessionLiveSync } from '$lib/stores/sessionLiveSync';
	import { registerLiveSyncAnchor } from '$lib/stores/liveSyncAnchors';
	import { tabsStore } from '$lib/stores/tabs';
	import { findGenerationTab } from '$lib/types/tabs';
	import LiveSyncChoice from './LiveSyncChoice.svelte';

	let {
		tabId,
		fieldKey,
		children
	}: {
		tabId: string | null | undefined;
		fieldKey: string | null | undefined;
		children?: Snippet;
	} = $props();

	const marks = sessionLiveSync.marks;
	const mode = $derived(tabId ? findGenerationTab($tabsStore.tabs, tabId)?.selectedMode : null);
	const tabMarks = $derived(tabId ? $marks[tabId] : undefined);
	const flash = $derived(fieldKey ? tabMarks?.highlights[fieldKey] : undefined);
	const conflict = $derived(
		fieldKey && mode ? tabMarks?.conflicts.find((item) => item.mode === mode && item.key === fieldKey) : undefined
	);

	$effect(() => {
		if (!tabId || !fieldKey) return;
		return registerLiveSyncAnchor(tabId, fieldKey);
	});
</script>

<div class="live-mark" role="presentation" data-live-key={fieldKey || undefined}>
	{@render children?.()}
	{#if flash}
		{#key flash}
			<span class="live-flash" aria-hidden="true" data-testid="live-sync-highlight"></span>
		{/key}
	{/if}
	{#if conflict && tabId}
		<LiveSyncChoice
			label={conflict.label}
			onTheirs={() => sessionLiveSync.resolve(tabId, conflict.id, 'theirs')}
			onMine={() => sessionLiveSync.resolve(tabId, conflict.id, 'mine')}
		/>
	{/if}
</div>

<style>
	.live-mark {
		position: relative;
	}

	.live-flash {
		position: absolute;
		inset: -3px;
		border-radius: 6px;
		pointer-events: none;
		box-shadow: 0 0 0 1px rgb(var(--signal) / 0.75);
		background: rgb(var(--signal) / 0.08);
		animation: live-flash-fade 2s ease-out forwards;
	}

	@keyframes live-flash-fade {
		0%,
		45% {
			opacity: 1;
		}
		100% {
			opacity: 0;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.live-flash {
			animation: none;
		}
	}
</style>
