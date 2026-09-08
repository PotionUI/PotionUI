<script lang="ts">
	import { api } from '$lib/services/api/index';
	import { Badge } from '$lib/components/ui';
	import LiveReferenceDataShell from './LiveReferenceDataShell.svelte';
	import DisclosureRow from './DisclosureRow.svelte';
	import {
		isDynamicMessageType,
		matchesOutputType,
		outputTypeFields,
		type OutputTypeEntry
	} from './outputTypesReference';

	let expanded: Record<string, boolean> = {};

	function toggle(key: string) {
		expanded = { ...expanded, [key]: !expanded[key] };
	}

	async function load(): Promise<OutputTypeEntry[]> {
		const response = await api.getDocsLiveOutputTypes();
		if (response.success && response.data) {
			const raw = response.data;
			return Array.isArray(raw) ? raw : raw.output_types || raw.types || [];
		}
		throw new Error(response.message || response.error || 'Failed to load output types reference');
	}
</script>

<p class="text-sm text-fg-muted mb-4">
	Every entry is one <code class="font-mono">GenerationOutput</code> subclass a pipe can emit through
	its <code class="font-mono">generation_outputs</code> callable (<code class="font-mono"
		>src/pipelines/outputs.py</code
	>). <code class="font-mono">message_type</code> is the WebSocket message type it serializes to;
	"dynamic" means it is derived per-instance rather than fixed.
</p>

<LiveReferenceDataShell {load} filter={matchesOutputType} label="output types">
	{#snippet content({ items })}
		<div class="space-y-2">
			{#each items as entry (entry.key)}
				{@const fields = outputTypeFields(entry)}
				{@const dynamic = isDynamicMessageType(entry.message_type)}
				<DisclosureRow expanded={!!expanded[entry.key]} onToggle={() => toggle(entry.key)}>
					{#snippet trigger()}
						<div class="min-w-0 flex-1">
							<div class="flex flex-wrap items-center gap-2">
								<code class="text-sm font-mono text-fg">{entry.key}</code>
								<span class="text-xs text-fg-subtle font-mono">{entry.output_class}</span>
								<Badge variant={dynamic ? 'warning' : 'neutral'} size="sm">
									{dynamic ? 'dynamic' : entry.message_type}
								</Badge>
								{#if entry.has_handler}<Badge variant="info" size="sm">handler</Badge>{/if}
								{#if entry.has_serializer}<Badge variant="signal" size="sm">serializer</Badge>{/if}
							</div>
							{#if entry.description}
								<p class="text-xs text-fg-muted truncate">{entry.description}</p>
							{/if}
						</div>
					{/snippet}

					{#if fields.length === 0}
						<p class="text-xs text-fg-subtle">No fields.</p>
					{:else}
						<div class="overflow-x-auto">
							<table class="w-full text-sm">
								<thead>
									<tr class="text-left text-fg-subtle text-xs">
										<th class="pr-3 pb-1 font-medium">Field</th>
										<th class="pr-3 pb-1 font-medium">Type</th>
										<th class="pb-1 font-medium">Default</th>
									</tr>
								</thead>
								<tbody>
									{#each fields as field (field.name)}
										<tr class="border-t border-line/60 align-top">
											<td class="pr-3 py-1.5 font-mono text-fg whitespace-nowrap">{field.name}</td>
											<td class="pr-3 py-1.5 font-mono text-fg-muted whitespace-nowrap"
												>{field.type}</td
											>
											<td class="py-1.5 font-mono tabular-nums text-fg-muted whitespace-nowrap">
												{field.default !== undefined && field.default !== null
													? JSON.stringify(field.default)
													: '—'}
											</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
				</DisclosureRow>
			{/each}
		</div>
	{/snippet}
</LiveReferenceDataShell>
