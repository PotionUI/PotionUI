<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Switch } from '$lib/components/ui';
	import type { TextMatcher } from '$lib/utils/textMatch';
	import type { McpTool, McpToolsLevel } from '$lib/services/api/mcpTools';
	import McpToolRow from './McpToolRow.svelte';
	import type { ToolGroup } from './mcpToolsView';

	let {
		group,
		level,
		open,
		matcher,
		onToggleOpen,
		onToggleTool,
		onToggleGroup,
		onChangeDefault
	}: {
		group: ToolGroup;
		level: McpToolsLevel;
		open: boolean;
		matcher: TextMatcher | null;
		onToggleOpen: (name: string) => void;
		onToggleTool: (tool: McpTool, on: boolean) => void;
		onToggleGroup: (group: ToolGroup, on: boolean) => void;
		onChangeDefault?: () => void;
	} = $props();

	const uid = $props.id();
</script>

<section data-group={group.name}>
	<div class="flex items-center gap-2 pr-3">
		<button
			type="button"
			aria-expanded={open}
			aria-controls="{uid}-rows"
			class="flex min-w-0 flex-1 items-center gap-2 px-3 py-2.5 text-left transition-colors hover:bg-surface-2"
			onclick={() => onToggleOpen(group.name)}
		>
			<Icon name="chevron-down" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle transition-transform {open ? '' : '-rotate-90'}" />
			<span class="truncate text-sm font-semibold text-fg">{group.name}</span>
			<span class="flex-shrink-0 font-mono text-sm tabular-nums text-fg-subtle">{group.on}/{group.denominator}</span>
		</button>
		<Switch
			checked={group.allOn}
			mixed={group.mixed}
			disabled={group.toggleable.length === 0}
			label="{group.name}: every available tool"
			onchange={(on) => onToggleGroup(group, on)}
		/>
	</div>
	<div id="{uid}-rows">
		{#if open}
			{#each group.shown as tool (tool.name)}
				<McpToolRow {tool} {level} {matcher} onToggle={onToggleTool} {onChangeDefault} />
			{/each}
		{/if}
	</div>
</section>
