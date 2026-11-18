<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, EmptyState, Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import { sourceEntries, type SourceEntryId } from '$lib/tool-tabs/sourceEntries';
	import type { MediaRef } from '$lib/types/tabs';
	import ImageSourcePicker from './ImageSourcePicker.svelte';
	import RecentEditsDialog from './RecentEditsDialog.svelte';

	let {
		tabId,
		phone = false,
		busy = false,
		error = null,
		onpick,
		onfile
	}: {
		tabId: string;
		phone?: boolean;
		busy?: boolean;
		error?: string | null;
		onpick: (source: MediaRef) => void;
		onfile: (file: File) => void;
	} = $props();

	const ENTRY_ORDER: SourceEntryId[] = ['browse', 'history', 'library', 'kits'];

	let picker = $state<ReturnType<typeof ImageSourcePicker>>();
	let dragging = $state(false);
	let editCount = $state(0);
	let recentOpen = $state(false);

	let entries = $derived.by(() => {
		const all = sourceEntries('image', phone);
		const ordered = ENTRY_ORDER.flatMap((id) => all.filter((entry) => entry.id === id));
		return ordered.map((entry) => (entry.id === 'browse' ? { ...entry, label: 'Upload file' } : entry));
	});

	async function countEdits() {
		try {
			const response = await api.listEdits({ tool: 'image', limit: 1, offset: 0 });
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

	function leave(event: DragEvent) {
		const next = event.relatedTarget;
		if (next instanceof Node && (event.currentTarget as HTMLElement).contains(next)) return;
		dragging = false;
	}

	function drop(event: DragEvent) {
		event.preventDefault();
		dragging = false;
		const file = Array.from(event.dataTransfer?.files ?? []).find((candidate) => candidate.type.startsWith('image/'));
		if (file) onfile(file);
	}
</script>

<div
	class="relative flex min-h-0 min-w-0 flex-1 flex-col"
	role="region"
	aria-label="Open a picture"
	ondragover={(event) => {
		event.preventDefault();
		dragging = true;
	}}
	ondragleave={leave}
	ondrop={drop}
	data-image-empty
	data-testid="tool-tab-empty"
	data-dragging={dragging ? '' : undefined}
>
	<div class="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto overflow-x-hidden bg-canvas dot-grid" data-start-screen>
		<EmptyState
			page
			icon="image"
			title="Open a picture to edit"
			description="Upload a picture or pick one from History, Library or Kits. Paste or drop one anywhere."
		>
			{#snippet actions()}
				<div class="flex flex-wrap items-center justify-center gap-3 max-md:gap-2" data-start-sources>
					{#each entries as entry (entry.id)}
						<Button
							variant="secondary"
							icon={entry.icon}
							ariaLabel={entry.label}
							attrs={{ 'data-start-source': entry.id }}
							onclick={() => picker?.open(entry.id)}
						>
							{entry.label}
						</Button>
					{/each}
					{#if editCount > 0}
						<Button variant="secondary" icon="clock" attrs={{ 'data-start-recent': '' }} onclick={() => (recentOpen = true)}>
							Recent edits <span class="font-mono tabular-nums text-fg-subtle" data-recent-count>{editCount}</span>
						</Button>
					{/if}
				</div>
				{#if busy}<span class="flex w-full justify-center"><Spinner /></span>{/if}
				{#if error}<p class="w-full text-sm text-danger" role="alert">{error}</p>{/if}
			{/snippet}
		</EmptyState>
	</div>

	{#if dragging}
		<div class="pointer-events-none absolute inset-0 z-40 flex items-center justify-center bg-signal/5 ring-1 ring-inset ring-signal max-md:hidden" aria-hidden="true" data-drop-overlay>
			<div class="flex flex-col items-center gap-1 rounded-xl border border-signal/60 bg-surface-1 px-10 py-6 text-center shadow-overlay">
				<span class="mb-2 flex h-12 w-12 items-center justify-center rounded-lg border border-signal/40 bg-signal/10 text-signal">
					<Icon name="upload" className="h-5 w-5" />
				</span>
				<span class="text-md font-semibold text-fg">Drop a picture to start an edit</span>
				<span class="text-sm text-fg-muted">Release it anywhere on this screen.</span>
			</div>
		</div>
	{/if}
</div>

<ImageSourcePicker bind:this={picker} {onpick} {onfile} />

{#if recentOpen}
	<RecentEditsDialog {tabId} onclose={closeRecent} />
{/if}
