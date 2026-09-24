<script lang="ts">
	import { Badge, Switch } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import LibraryEntryCard from '$lib/components/library/LibraryEntryCard.svelte';
	import { resolveCategory } from '$lib/plugins/categories';
	import type { Plugin } from '$lib/stores/plugins';

	let {
		plugin,
		busy = false,
		dense = false,
		onOpen,
		onToggle
	}: {
		plugin: Plugin;
		busy?: boolean;
		dense?: boolean;
		onOpen: (plugin: Plugin) => void;
		onToggle: (plugin: Plugin) => void;
	} = $props();

	const category = $derived(resolveCategory(plugin.category));
	const isError = $derived(plugin.state === 'error');
	const muted = $derived(!plugin.enabled || isError);
	const description = $derived(isError ? plugin.error || 'Invalid manifest' : plugin.description || 'No description available');

	function stopToggle(event: Event) {
		event.stopPropagation();
	}
</script>

<LibraryEntryCard
	icon={category.icon}
	name={plugin.name}
	{dense}
	{muted}
	{description}
	onOpen={() => onOpen(plugin)}
	ariaLabel={plugin.name}
>
	{#snippet nameSuffix()}
		<span class="flex-shrink-0 font-mono text-xs text-fg-subtle">v{plugin.version}</span>
	{/snippet}
	{#snippet topRight()}
		<div class="flex-shrink-0" role="presentation" onclick={stopToggle} onkeydown={stopToggle}>
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
	{/snippet}
	{#snippet footer()}
		<Badge class="font-sans font-semibold">{category.label}</Badge>
		{#if isError}
			<Tooltip text={plugin.error || 'Invalid manifest'}>
				<Badge variant="danger" dot>Error</Badge>
			</Tooltip>
		{:else}
			<Badge class="font-mono uppercase">{plugin.type}</Badge>
		{/if}
	{/snippet}
</LibraryEntryCard>
