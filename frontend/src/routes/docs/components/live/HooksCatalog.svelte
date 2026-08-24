<script lang="ts">
	import { api } from '$lib/services/api/index';
	import { Badge } from '$lib/components/ui';
	import LiveReferenceDataShell from './LiveReferenceDataShell.svelte';

	interface HookPayloadField {
		type?: string;
		description?: string;
		[key: string]: unknown;
	}

	interface HookSpec {
		name: string;
		type?: string;
		description?: string;
		payload?: Record<string, HookPayloadField>;
		mutable?: string[];
		use_when?: string[];
		example?: string;
		[key: string]: unknown;
	}

	let expanded = new Set<string>();

	// Hook names are conventionally "domain.action" (e.g. "generation.started");
	// group by the segment before the first dot, falling back to "other".
	function domainOf(name: string): string {
		const idx = name.indexOf('.');
		return idx === -1 ? 'other' : name.slice(0, idx);
	}

	function badgeVariant(type?: string): 'signal' | 'info' | 'neutral' {
		if (type === 'backend') return 'signal';
		if (type === 'frontend') return 'info';
		return 'neutral';
	}

	function toggle(name: string) {
		if (expanded.has(name)) {
			expanded.delete(name);
		} else {
			expanded.add(name);
		}
		expanded = expanded;
	}

	function payloadEntries(hook: HookSpec): [string, HookPayloadField][] {
		return Object.entries(hook.payload || {});
	}

	function hasDocs(hook: HookSpec): boolean {
		return payloadEntries(hook).length > 0 || (hook.use_when || []).length > 0;
	}

	function matches(hook: HookSpec, query: string): boolean {
		const needle = query.toLowerCase();
		return (
			hook.name.toLowerCase().includes(needle) ||
			(hook.description || '').toLowerCase().includes(needle) ||
			(hook.use_when || []).some((u) => u.toLowerCase().includes(needle))
		);
	}

	function groupByDomain(hooks: HookSpec[]): Record<string, HookSpec[]> {
		return hooks.reduce<Record<string, HookSpec[]>>((acc, hook) => {
			const domain = domainOf(hook.name);
			(acc[domain] ||= []).push(hook);
			return acc;
		}, {});
	}

	async function load(): Promise<HookSpec[]> {
		const response = await api.getHooksCatalog();
		if (response.success && response.data) {
			const raw = response.data;
			return Array.isArray(raw) ? raw : raw.hooks || [];
		}
		throw new Error(response.message || response.error || 'Failed to load hooks catalog');
	}
</script>

<LiveReferenceDataShell {load} filter={matches} label="hooks">
	{#snippet content({ items })}
		{@const groups = groupByDomain(items)}
		{@const sortedDomains = Object.keys(groups).sort()}
		<div class="space-y-6">
			{#each sortedDomains as domain (domain)}
				<div>
					<h3 class="text-xs font-semibold uppercase tracking-wide text-fg-muted mb-2">{domain}</h3>
					<div class="space-y-2">
						{#each groups[domain] as hook (hook.name)}
							{@const isOpen = expanded.has(hook.name)}
							<div class="border border-line rounded-lg bg-surface-1">
								<button
									type="button"
									class="w-full text-left p-3 flex items-center gap-2 flex-wrap"
									aria-expanded={isOpen}
									onclick={() => toggle(hook.name)}
								>
									<code class="text-sm font-mono text-fg">{hook.name}</code>
									{#if hook.type}
										<Badge variant={badgeVariant(hook.type)} size="sm">{hook.type}</Badge>
									{/if}
									{#if hook.description}
										<span class="text-sm text-fg-muted">{hook.description}</span>
									{/if}
									<span class="ml-auto text-fg-subtle text-xs">{isOpen ? '▲' : '▼'}</span>
								</button>

								{#if isOpen}
									<div class="px-3 pb-3 space-y-3 border-t border-line pt-3">
										{#if (hook.use_when || []).length > 0}
											<div>
												<h4 class="text-2xs font-semibold uppercase tracking-wide text-fg-subtle mb-1">
													Use this when
												</h4>
												<ul class="list-disc list-inside text-sm text-fg-muted space-y-0.5">
													{#each hook.use_when || [] as bullet}
														<li>{bullet}</li>
													{/each}
												</ul>
											</div>
										{/if}

										{#if payloadEntries(hook).length > 0}
											<div>
												<h4 class="text-2xs font-semibold uppercase tracking-wide text-fg-subtle mb-1">
													Payload
												</h4>
												<div class="overflow-x-auto">
													<table class="w-full text-sm">
														<thead>
															<tr class="text-left text-fg-subtle text-xs">
																<th class="pr-3 pb-1 font-medium">Key</th>
																<th class="pr-3 pb-1 font-medium">Type</th>
																<th class="pb-1 font-medium">Description</th>
															</tr>
														</thead>
														<tbody>
															{#each payloadEntries(hook) as [key, field]}
																<tr class="border-t border-line/60">
																	<td class="pr-3 py-1 font-mono text-fg align-top whitespace-nowrap">
																		{key}
																		{#if (hook.mutable || []).includes(key)}
																			<Badge variant="warning" size="sm" class="ml-1">mutable</Badge>
																		{/if}
																	</td>
																	<td class="pr-3 py-1 text-fg-muted align-top whitespace-nowrap">{field.type || ''}</td>
																	<td class="py-1 text-fg-muted align-top">{field.description || ''}</td>
																</tr>
															{/each}
														</tbody>
													</table>
												</div>
											</div>
										{/if}

										{#if hook.example}
											<div>
												<h4 class="text-2xs font-semibold uppercase tracking-wide text-fg-subtle mb-1">
													Example
												</h4>
												<pre class="text-xs bg-surface-2 border border-line rounded p-2 overflow-x-auto"><code
													>{hook.example}</code
												></pre>
											</div>
										{/if}

										{#if !hasDocs(hook)}
											<p class="text-xs text-fg-subtle italic">No payload documentation yet.</p>
										{/if}
									</div>
								{/if}
							</div>
						{/each}
					</div>
				</div>
			{/each}
		</div>
	{/snippet}
</LiveReferenceDataShell>
