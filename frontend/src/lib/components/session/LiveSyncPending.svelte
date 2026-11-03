<script lang="ts">
	import { sessionLiveSync, type PendingConflict } from '$lib/stores/sessionLiveSync';
	import { liveSyncAnchors } from '$lib/stores/liveSyncAnchors';
	import { tabsStore } from '$lib/stores/tabs';
	import { findGenerationTab } from '$lib/types/tabs';
	import { describeSyncField } from '$lib/session/sessionMerge';
	import LiveSyncChoice from './LiveSyncChoice.svelte';

	let { tabId }: { tabId: string } = $props();

	const marks = sessionLiveSync.marks;
	const mode = $derived(findGenerationTab($tabsStore.tabs, tabId)?.selectedMode ?? null);
	const anchored = $derived($liveSyncAnchors[tabId] ?? {});
	const loose = $derived(
		($marks[tabId]?.conflicts ?? []).filter(
			(conflict: PendingConflict) => conflict.mode !== mode || !anchored[conflict.key]
		)
	);

	function detail(conflict: PendingConflict): string {
		const field = describeSyncField(conflict.key, conflict.remote ?? conflict.local);
		return conflict.mode === mode ? field : `${conflict.mode} · ${field}`;
	}
</script>

{#if loose.length > 0}
	<div class="mb-2 space-y-1" data-testid="live-sync-pending">
		{#each loose as conflict (conflict.id)}
			<LiveSyncChoice
				label={conflict.label}
				detail={detail(conflict)}
				onTheirs={() => sessionLiveSync.resolve(tabId, conflict.id, 'theirs')}
				onMine={() => sessionLiveSync.resolve(tabId, conflict.id, 'mine')}
			/>
		{/each}
	</div>
{/if}
