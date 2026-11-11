<script lang="ts">
	import HighlightedText from '$lib/components/HighlightedText.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Switch } from '$lib/components/ui';
	import tooltip from '$lib/actions/tooltip';
	import type { TextMatcher } from '$lib/utils/textMatch';
	import type { McpTool, McpToolsLevel } from '$lib/services/api/mcpTools';
	import { showsNewChip, toolKind } from './mcpToolsView';

	let {
		tool,
		level,
		matcher,
		onToggle,
		onChangeDefault
	}: {
		tool: McpTool;
		level: McpToolsLevel;
		matcher: TextMatcher | null;
		onToggle: (tool: McpTool, on: boolean) => void;
		onChangeDefault?: () => void;
	} = $props();

	const kind = $derived(toolKind(tool));
	const switchLabel = $derived(
		kind === 'unavailable'
			? `${tool.name} is unavailable over MCP`
			: kind === 'admin'
				? `${tool.name} is turned off by your administrator`
				: kind === 'offDefault'
					? `${tool.name} is off in your default`
					: `${tool.name} ${level === 'ceiling' ? 'allowed over MCP' : level === 'key' ? 'on for this key' : 'on by default'}`
	);
</script>

<div
	class="flex items-start gap-3 border-t border-line py-2.5 pl-3 pr-3 sm:pl-9"
	data-tool={tool.name}
	data-state={kind === 'tool' ? tool.state : kind}
>
	<div class="min-w-0 flex-1">
		<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
			<code class="font-mono text-sm font-medium [overflow-wrap:anywhere] {kind === 'tool' ? 'text-fg' : 'text-fg-muted'}">
				<HighlightedText text={tool.name} {matcher} />
			</code>
			{#if tool.writes}
				<span
					use:tooltip={'Changes your data. PotionUI does not ask again over MCP — your client’s own confirmation is the only check.'}
					class="inline-flex cursor-default items-center rounded border border-warning/25 bg-warning/10 px-1.5 text-sm font-medium text-warning"
				>
					writes
				</span>
			{/if}
			{#if showsNewChip(tool)}
				<span
					use:tooltip={'Not reviewed yet. Follows the “New tools” setting until you switch it yourself.'}
					class="inline-flex cursor-default items-center rounded border border-line-strong bg-surface-2 px-1.5 text-sm font-medium text-fg-muted"
				>
					new
				</span>
			{/if}
		</div>
		<p class="mt-0.5 line-clamp-2 text-sm text-fg-muted" use:tooltip={{ text: tool.description, truncateOnly: true, wrap: true }}>
			<HighlightedText text={tool.description} {matcher} />
		</p>
		{#if kind === 'unavailable'}
			<p class="mt-1 flex items-start gap-1.5 text-sm text-fg-subtle">
				<Icon name="info" className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
				<span>{tool.unavailable_reason}</span>
			</p>
		{:else if kind === 'admin'}
			<p class="mt-1 flex items-start gap-1.5 text-sm text-fg-subtle">
				<Icon name="lock" className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
				<span>Turned off by your administrator.</span>
			</p>
		{:else if kind === 'offDefault'}
			<p class="mt-1 flex flex-wrap items-start gap-x-1.5 text-sm text-fg-subtle">
				<Icon name="info" className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
				<span>Off in your default.</span>
				<button type="button" class="text-signal hover:underline" onclick={() => onChangeDefault?.()}>Change default</button>
			</p>
		{/if}
	</div>
	<div class="pt-0.5">
		<Switch checked={kind === 'tool' && tool.state === 'on'} disabled={kind !== 'tool'} label={switchLabel} onchange={(on) => onToggle(tool, on)} />
	</div>
</div>
