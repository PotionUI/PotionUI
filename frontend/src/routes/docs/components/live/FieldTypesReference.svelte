<script lang="ts">
	import { api } from '$lib/services/api/index';
	import { Badge } from '$lib/components/ui';
	import LiveReferenceDataShell from './LiveReferenceDataShell.svelte';
	import {
		configurationOptions,
		isPluginSource,
		matchesFieldType,
		type FieldTypeEntry
	} from './fieldTypesReference';

	async function load(): Promise<FieldTypeEntry[]> {
		const response = await api.getDocsFieldTypes();
		if (response.success && response.data) {
			const raw = response.data;
			return Array.isArray(raw) ? raw : raw.types || raw.fields || [];
		}
		throw new Error(response.message || response.error || 'Failed to load field types');
	}
</script>

<p class="text-sm text-fg-muted mb-4">
	Every field also accepts the common field-spec keys (<code class="font-mono">label</code>,
	<code class="font-mono">description</code>, <code class="font-mono">default</code>,
	<code class="font-mono">required</code>, …) documented in the Preset Authoring Guide (Documentation
	→ Presets / Models). The options below are each field type's own configuration surface.
</p>

<LiveReferenceDataShell {load} filter={matchesFieldType} label="field types">
	{#snippet content({ items })}
		<div class="space-y-4">
			{#each items as entry (entry.type)}
				{@const options = configurationOptions(entry)}
				<div class="border border-line rounded-lg bg-surface-1">
					<div class="flex flex-wrap items-center gap-2 px-4 py-3 border-b border-line">
						<code class="text-sm font-mono font-semibold text-fg">{entry.type}</code>
						{#if entry.container}<Badge variant="info" size="sm">container</Badge>{/if}
						{#if isPluginSource(entry.source)}<Badge variant="signal" size="sm">{entry.source}</Badge>{/if}
						{#if entry.component}
							<span class="ml-auto text-xs text-fg-subtle font-mono">{entry.component}</span>
						{/if}
					</div>

					<div class="px-4 py-3">
						{#if options.length === 0}
							<p class="text-xs text-fg-subtle">No configuration options.</p>
						{:else}
							<div class="overflow-x-auto">
								<table class="w-full text-sm">
									<thead>
										<tr class="text-left text-fg-subtle text-xs">
											<th class="pr-3 pb-1 font-medium">Option</th>
											<th class="pr-3 pb-1 font-medium">Type</th>
											<th class="pr-3 pb-1 font-medium">Default</th>
											<th class="pb-1 font-medium">Description</th>
										</tr>
									</thead>
									<tbody>
										{#each options as option (option.name)}
											<tr class="border-t border-line/60 align-top">
												<td class="pr-3 py-1.5 font-mono text-fg whitespace-nowrap">
													{option.name}
													{#if option.required}<Badge variant="warning" size="sm" class="ml-1">required</Badge>{/if}
												</td>
												<td class="pr-3 py-1.5 font-mono text-fg-muted whitespace-nowrap">{option.param_type ?? '—'}</td>
												<td class="pr-3 py-1.5 font-mono tabular-nums text-fg-muted whitespace-nowrap">
													{option.default !== undefined && option.default !== null && option.default !== ''
														? JSON.stringify(option.default)
														: '—'}
												</td>
												<td class="py-1.5 text-fg-muted">
													{option.description || ''}
													{#if option.choices && option.choices.length > 0}
														<div class="mt-1 flex flex-wrap gap-1">
															{#each option.choices as choice}
																<code class="text-2xs font-mono bg-surface-2 border border-line rounded px-1 py-0.5"
																	>{choice}</code
																>
															{/each}
														</div>
													{/if}
													{#if option.example !== undefined && option.example !== null}
														<div class="mt-1 text-xs">
															<span class="text-fg-subtle">example:</span>
															<code class="font-mono">{JSON.stringify(option.example)}</code>
														</div>
													{/if}
												</td>
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/snippet}
</LiveReferenceDataShell>
