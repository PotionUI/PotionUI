<script lang="ts">
	import { onMount } from 'svelte';
	import { Button, EmptyState } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import { sourceEntries, type SourceEntryId } from '$lib/tool-tabs/sourceEntries';
	import RecentEditsDialog from '../image/RecentEditsDialog.svelte';

	let { tabId, onpick }: { tabId: string; onpick: (id: SourceEntryId) => void } = $props();

	let editCount = $state(0);
	let recentOpen = $state(false);

	async function countEdits() {
		try {
			const response = await api.listEdits({ tool: 'video', limit: 1, offset: 0 });
			editCount = response.success && response.data ? response.data.total : 0;
		} catch {
			editCount = 0;
		}
	}

	onMount(() => {
		void countEdits();
	});

	function closeRecent() {
		recentOpen = false;
		void countEdits();
	}
</script>

<div class="flex min-h-0 flex-1 flex-col dot-grid bg-canvas">
	<div class="flex flex-1 flex-col" data-testid="tool-tab-empty" data-start-screen>
		<EmptyState
			page
			icon="video"
			title="Open a clip to edit"
			description="Pick a video from your Library or History. Edit from History or Library opens it here too."
		>
			{#snippet actions()}
				<div class="flex flex-wrap items-center justify-center gap-3 max-md:gap-2" data-start-sources>
					{#each sourceEntries('video') as entry (entry.id)}
						<Button variant="secondary" icon={entry.icon} attrs={{ 'data-start-source': entry.id }} onclick={() => onpick(entry.id)}>{entry.label}</Button>
					{/each}
					{#if editCount > 0}
						<Button variant="secondary" icon="clock" attrs={{ 'data-start-recent': '' }} onclick={() => (recentOpen = true)}>
							Recent edits <span class="font-mono tabular-nums text-fg-subtle" data-recent-count>{editCount}</span>
						</Button>
					{/if}
				</div>
			{/snippet}
		</EmptyState>
	</div>
</div>

{#if recentOpen}
	<RecentEditsDialog {tabId} tool="video" onclose={closeRecent} />
{/if}
