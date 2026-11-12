<script lang="ts">
	import { onMount } from 'svelte';
	import { Alert, Button } from '$lib/components/ui';
	import { sessionChange, type SessionChange } from '$lib/kits/kitChips';
	import { snapshotOf, splitKitRows, withKitRows } from '$lib/kits/kitMarker';
	import { kitFieldsOf, type PromptSpecLike } from '$lib/kits/promptKits';
	import { requestKitLoad } from '$lib/stores/kitLoadRequests';
	import { ensureKits, kitDirectory } from '$lib/stores/kits';
	import type { KitRow } from '$lib/kits/types';
	import KitPath from './KitPath.svelte';

	let {
		formData,
		specs,
		onChange
	}: {
		formData: Record<string, unknown> | null | undefined;
		specs: readonly PromptSpecLike[];
		onChange: (field: string, value: unknown[]) => void;
	} = $props();

	let fields = $derived(kitFieldsOf(specs, formData));
	let changes = $derived(
		fields.flatMap((entry) =>
			entry.rows
				.map((row) => ({ field: entry.field, row, change: sessionChange(row, $kitDirectory) }))
				.filter((found): found is { field: string; row: KitRow; change: SessionChange } => found.change !== null)
		)
	);

	function write(field: string, mapRows: (rows: KitRow[]) => KitRow[]) {
		const entry = fields.find((candidate) => candidate.field === field);
		if (!entry) return;
		const value = formData?.[field];
		const media = splitKitRows(Array.isArray(value) ? (value as unknown[]) : []).media;
		onChange(field, withKitRows(media, mapRows(entry.rows)));
	}

	function acknowledge(field: string, rowId: string) {
		write(field, (rows) =>
			rows.map((row) => {
				const kit = $kitDirectory.get(row.kit);
				return row.kit === rowId && kit ? { kit: row.kit, snapshot: snapshotOf(kit) } : row;
			})
		);
	}

	function drop(field: string, rowId: string) {
		write(field, (rows) => rows.filter((row) => row.kit !== rowId));
	}

	onMount(() => {
		const ids = fields.flatMap((entry) => entry.rows.map((row) => row.kit));
		if (ids.length > 0) void ensureKits(ids, { refresh: true });
	});
</script>

{#each changes as { field, row, change } (row.kit)}
	{#if change.kind === 'deleted'}
		<Alert variant="danger" icon title="{change.name} no longer exists" density="compact">
			<p>The kit this prompt calls was deleted. Its chips stay in place and are left out. Choose another kit, or remove them.</p>
			{#snippet actions()}
				<Button variant="secondary" size="xs" onclick={() => requestKitLoad(field)}>Choose another kit</Button>
				<Button variant="ghost" size="xs" onclick={() => drop(field, row.kit)}>Remove from this session</Button>
			{/snippet}
		</Alert>
	{:else}
		<Alert variant="info" icon title="{change.name} changed since this session was saved" density="compact">
			<div class="space-y-1">
				{#if change.kitRenamed}
					<p><KitPath handle={change.kitRenamed.from} class="text-fg-muted" /> is now <KitPath handle={change.kitRenamed.to} class="text-fg" />. Chips follow it.</p>
				{/if}
				{#each change.itemsRenamed as renamed (renamed.from)}
					<p><span class="font-mono text-fg">{renamed.from}</span> is now <span class="font-mono text-fg">{renamed.to}</span>.</p>
				{/each}
				{#each change.itemsRemoved as removed (removed)}
					<p><span class="font-mono text-fg">{removed}</span> was removed. Its chip is left out.</p>
				{/each}
			</div>
			{#snippet actions()}
				<Button variant="secondary" size="xs" onclick={() => acknowledge(field, row.kit)}>Got it</Button>
			{/snippet}
		</Alert>
	{/if}
{/each}
