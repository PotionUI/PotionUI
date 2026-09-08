<script lang="ts">
	import { api } from '$lib/services/api/index';
	import { Badge } from '$lib/components/ui';
	import LiveReferenceDataShell from './LiveReferenceDataShell.svelte';
	import DisclosureRow from './DisclosureRow.svelte';
	import {
		matchesMcpTool,
		toolParameters,
		type McpGovernance,
		type McpToolEntry
	} from './mcpToolsReference';

	let expanded: Record<string, boolean> = {};
	let governance = $state<McpGovernance>({});

	function toggle(name: string) {
		expanded = { ...expanded, [name]: !expanded[name] };
	}

	async function load(): Promise<McpToolEntry[]> {
		const response = await api.getDocsLiveMcpTools();
		if (response.success && response.data) {
			const raw = response.data;
			governance = raw.governance ?? {};
			return Array.isArray(raw) ? raw : raw.tools || [];
		}
		throw new Error(response.message || response.error || 'Failed to load MCP tools reference');
	}
</script>

<p class="text-sm text-fg-muted mb-4">
	Every entry is a tool an MCP client can call over <code class="font-mono">/api/mcp</code> once
	authenticated with a per-user token (<strong>Settings</strong>) — the same set the server's
	<code class="font-mono">tools/list</code> method returns.
</p>

{#if governance.acts_as_token_owner || governance.model_visibility_rule}
	<div class="mb-4 rounded-lg border border-line bg-surface-1 px-4 py-3 space-y-2 text-sm">
		{#if governance.acts_as_token_owner}
			<p class="text-fg-muted">{governance.acts_as_token_owner}</p>
		{/if}
		{#if governance.model_visibility_rule}
			<p class="text-fg-muted">{governance.model_visibility_rule}</p>
		{/if}
		<div class="flex flex-wrap gap-4 pt-1 text-xs text-fg-subtle">
			{#if governance.global_setting_key}
				<span
					>Global switch <code class="font-mono text-fg-muted">{governance.global_setting_key}</code
					>, off by default.</span
				>
			{/if}
			{#if governance.user_setting_key}
				<span
					>Per-user switch <code class="font-mono text-fg-muted">{governance.user_setting_key}</code
					>, on by default once MCP is on.</span
				>
			{/if}
		</div>
		{#if governance.excluded_tool_names && governance.excluded_tool_names.length > 0}
			<div class="pt-1">
				<span class="text-xs text-fg-subtle"
					>Never exposed over MCP ({governance.excluded_tool_names.length}, form-state only):</span
				>
				<div class="flex flex-wrap gap-1 mt-1">
					{#each governance.excluded_tool_names as name (name)}
						<code class="text-2xs font-mono bg-surface-2 border border-line rounded px-1 py-0.5"
							>{name}</code
						>
					{/each}
				</div>
			</div>
		{/if}
	</div>
{/if}

<LiveReferenceDataShell {load} filter={matchesMcpTool} label="MCP tools">
	{#snippet content({ items })}
		<div class="space-y-2">
			{#each items as entry (entry.name)}
				{@const parameters = toolParameters(entry)}
				<DisclosureRow expanded={!!expanded[entry.name]} onToggle={() => toggle(entry.name)}>
					{#snippet trigger()}
						<div class="min-w-0 flex-1">
							<div class="flex flex-wrap items-center gap-2">
								<code class="text-sm font-mono text-fg">{entry.name}</code>
								{#if entry.group}<Badge variant="neutral" size="sm">{entry.group}</Badge>{/if}
								{#if entry.mutating}
									<Badge variant="warning" size="sm">mutating</Badge>
								{:else}
									<Badge variant="success" size="sm">read-only</Badge>
								{/if}
							</div>
							{#if entry.description}
								<p class="text-xs text-fg-muted truncate">{entry.description}</p>
							{/if}
						</div>
					{/snippet}

					{#if parameters.length === 0}
						<p class="text-xs text-fg-subtle">No parameters.</p>
					{:else}
						<div class="overflow-x-auto">
							<table class="w-full text-sm">
								<thead>
									<tr class="text-left text-fg-subtle text-xs">
										<th class="pr-3 pb-1 font-medium">Parameter</th>
										<th class="pr-3 pb-1 font-medium">Type</th>
										<th class="pr-3 pb-1 font-medium">Required</th>
										<th class="pb-1 font-medium">Description</th>
									</tr>
								</thead>
								<tbody>
									{#each parameters as param (`${entry.name}:${param.name}`)}
										<tr class="border-t border-line/60 align-top">
											<td class="pr-3 py-1.5 font-mono text-fg whitespace-nowrap">{param.name}</td>
											<td class="pr-3 py-1.5 font-mono text-fg-muted whitespace-nowrap"
												>{param.type ?? 'any'}</td
											>
											<td class="pr-3 py-1.5 whitespace-nowrap">
												{#if param.required}
													<Badge variant="warning" size="sm">required</Badge>
												{:else}
													<span class="text-fg-subtle">optional</span>
												{/if}
											</td>
											<td class="py-1.5 text-fg-muted">{param.description || ''}</td>
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
