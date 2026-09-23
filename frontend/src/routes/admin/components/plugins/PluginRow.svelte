<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, Switch } from '$lib/components/ui';
	import { resolveCategory } from '$lib/plugins/categories';
	import type { Plugin } from '$lib/stores/plugins';

	let {
		plugin,
		busy = false,
		onOpen,
		onToggle
	}: {
		plugin: Plugin;
		busy?: boolean;
		onOpen: (plugin: Plugin) => void;
		onToggle: (plugin: Plugin) => void;
	} = $props();

	const category = $derived(resolveCategory(plugin.category));
	const isError = $derived(plugin.state === 'error');

	function handleKeydown(event: KeyboardEvent) {
		if (event.key !== 'Enter' && event.key !== ' ') return;
		event.preventDefault();
		onOpen(plugin);
	}

	function stopPropagation(event: Event) {
		event.stopPropagation();
	}
</script>

<div
	class="flex w-full items-start gap-3 rounded-lg border border-line bg-surface-1 p-3.5 text-left transition-colors hover:border-line-hover cursor-pointer"
	role="option"
	aria-selected="false"
	tabindex="0"
	onclick={() => onOpen(plugin)}
	onkeydown={handleKeydown}
>
	<span class="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border border-line bg-surface-2 text-fg-muted">
		<Icon name={category.icon} className="h-4 w-4" />
	</span>

	<div class="min-w-0 flex-1">
		<div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
			<span class="truncate text-sm font-semibold {plugin.enabled ? 'text-fg' : 'text-fg-muted'}">{plugin.name}</span>
			<span class="flex-shrink-0 font-mono text-xs text-fg-subtle">{plugin.id}</span>
			<span class="flex-shrink-0 font-mono text-xs tabular-nums text-fg-subtle">v{plugin.version}</span>
		</div>

		<Tooltip text={plugin.description || 'No description available'} wrapperClass="flex w-full min-w-0">
			<p class="mt-1 line-clamp-2 text-xs leading-relaxed text-fg-muted">
				{plugin.description || 'No description available'}
			</p>
		</Tooltip>

		<div class="mt-2 flex flex-wrap items-center gap-1.5">
			<Badge variant="neutral" size="sm" class="font-mono uppercase">{plugin.type}</Badge>
			{#if isError}
				<Tooltip text={plugin.error || 'Invalid manifest'}>
					<Badge variant="danger" size="sm" dot class="uppercase">Error</Badge>
				</Tooltip>
			{/if}
			{#each plugin.capabilities ?? [] as capability (capability)}
				<Badge variant="signal" size="sm" class="font-mono">{capability}</Badge>
			{/each}
			{#each plugin.tags ?? [] as tag (tag)}
				<Badge variant="neutral" size="sm">{tag}</Badge>
			{/each}
		</div>
	</div>

	<div class="flex-shrink-0" role="presentation" onclick={stopPropagation} onkeydown={stopPropagation}>
		<Switch
			checked={plugin.enabled}
			{busy}
			disabled={isError}
			size="lg"
			onchange={() => onToggle(plugin)}
			label={isError
				? `${plugin.name} has an invalid manifest and cannot be enabled`
				: plugin.enabled
					? `Disable ${plugin.name}`
					: `Enable ${plugin.name}`}
		/>
	</div>
</div>
