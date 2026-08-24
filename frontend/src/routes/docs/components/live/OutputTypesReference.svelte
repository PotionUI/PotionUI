<script lang="ts">
	import { api } from '$lib/services/api/index';
	import LiveReferenceDataShell from './LiveReferenceDataShell.svelte';
	import DisclosureRow from './DisclosureRow.svelte';

	interface OutputTypeSpec {
		name?: string;
		type?: string;
		description?: string;
		fields?: unknown;
		[key: string]: unknown;
	}

	let expanded: Record<string, boolean> = {};

	function typeKey(entry: OutputTypeSpec, index: number): string {
		return String(entry.name ?? entry.type ?? index);
	}

	function typeLabel(entry: OutputTypeSpec): string {
		return String(entry.name ?? entry.type ?? 'unknown');
	}

	function toggle(key: string) {
		expanded = { ...expanded, [key]: !expanded[key] };
	}

	function matches(entry: OutputTypeSpec, query: string): boolean {
		const needle = query.toLowerCase();
		return (
			typeLabel(entry).toLowerCase().includes(needle) ||
			(entry.description || '').toLowerCase().includes(needle)
		);
	}

	async function load(): Promise<OutputTypeSpec[]> {
		const response = await api.getDocsLiveOutputTypes();
		if (response.success && response.data) {
			const raw = response.data;
			return Array.isArray(raw) ? raw : raw.output_types || raw.types || [];
		}
		throw new Error(response.message || response.error || 'Failed to load output types reference');
	}
</script>

<LiveReferenceDataShell {load} filter={matches} label="output types">
	{#snippet content({ items })}
		<div class="space-y-2">
			{#each items as entry, index (typeKey(entry, index))}
				{@const key = typeKey(entry, index)}
				<DisclosureRow expanded={!!expanded[key]} onToggle={() => toggle(key)}>
					{#snippet trigger()}
						<div class="min-w-0">
							<code class="text-sm font-mono text-fg">{typeLabel(entry)}</code>
							{#if entry.description}
								<p class="text-xs text-fg-muted truncate">{entry.description}</p>
							{/if}
						</div>
					{/snippet}
					{#if entry.fields !== undefined}
						<pre class="text-xs font-mono bg-surface-2 rounded p-2 overflow-x-auto text-fg-muted">{JSON.stringify(entry.fields, null, 2)}</pre>
					{:else}
						<pre class="text-xs font-mono bg-surface-2 rounded p-2 overflow-x-auto text-fg-muted">{JSON.stringify(entry, null, 2)}</pre>
					{/if}
				</DisclosureRow>
			{/each}
		</div>
	{/snippet}
</LiveReferenceDataShell>
