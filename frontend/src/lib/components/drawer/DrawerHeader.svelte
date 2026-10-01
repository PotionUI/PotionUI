<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, IconButton, Switch } from '$lib/components/ui';

	let {
		title,
		count = undefined,
		closeLabel,
		keepOpen = undefined,
		onKeepOpenChange = undefined,
		onBack = undefined,
		onClose
	}: {
		title: string;
		count?: number;
		closeLabel: string;
		keepOpen?: boolean;
		onKeepOpenChange?: (next: boolean) => void;
		onBack?: () => void;
		onClose: () => void;
	} = $props();
</script>

<header class="flex h-[52px] flex-shrink-0 items-center gap-2 border-b border-line pl-4 pr-2">
	{#if onBack}
		<Button variant="ghost" size="sm" icon="chevron-left" onclick={onBack}>Back</Button>
		<h2 class="min-w-0 flex-1 truncate text-md font-semibold text-fg">{title}</h2>
	{:else}
		<h2 class="text-md font-semibold text-fg">{title}</h2>
		{#if count !== undefined}
			<span class="font-mono text-xs tabular-nums text-fg-subtle">{count}</span>
		{/if}
		<span class="flex-1"></span>
		{#if onKeepOpenChange}
			<label class="keep-open flex items-center gap-2 text-xs text-fg-muted">
				<span>Keep open</span>
				<Switch size="sm" label="Keep open" checked={keepOpen} onchange={onKeepOpenChange} />
			</label>
		{/if}
	{/if}
	<Tooltip text="Close" kbd="Esc" position="bottom" delay={150}>
		<IconButton icon="close" label={closeLabel} size="sm" onclick={onClose} />
	</Tooltip>
</header>

<style>
	@media (max-width: 640px) {
		.keep-open {
			display: none;
		}
	}
</style>
