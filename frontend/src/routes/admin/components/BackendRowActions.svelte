<script lang="ts">
	import { IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { INDEX_MODELS_ICON, stopRowEvent } from './backendIndexing';

	let {
		testing = false,
		indexing = false,
		showMakeDefault = false,
		size = 'md',
		onTest,
		onIndex,
		onMakeDefault
	}: {
		testing?: boolean;
		indexing?: boolean;
		showMakeDefault?: boolean;
		size?: 'sm' | 'md';
		onTest: () => void;
		onIndex: () => void;
		onMakeDefault?: () => void;
	} = $props();
</script>

<div
	class="flex items-center gap-1"
	role="presentation"
	data-testid="backend-row-actions"
	onclick={stopRowEvent}
	onkeydown={stopRowEvent}
>
	<Tooltip text={testing ? 'Testing…' : 'Test connection'}>
		<IconButton icon="check" label="Test connection" {size} loading={testing} onclick={onTest} />
	</Tooltip>
	<Tooltip text={indexing ? 'Indexing…' : 'Index models'}>
		<IconButton icon={INDEX_MODELS_ICON} label="Index models" {size} loading={indexing} onclick={onIndex} />
	</Tooltip>
	{#if showMakeDefault && onMakeDefault}
		<Tooltip text="Make default">
			<IconButton icon="star" label="Make default" {size} onclick={onMakeDefault} />
		</Tooltip>
	{/if}
</div>
