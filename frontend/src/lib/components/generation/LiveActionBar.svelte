<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import { Button } from '$lib/components/ui';
	import {
		GO_TO_TAB_LABEL,
		OPEN_EDIT_LABEL,
		OPEN_IN_TAB_LABEL,
		type OriginAction
	} from '$lib/generation/live/originAction';

	let {
		live,
		cancelling = false,
		onCancel,
		origin = null,
		onOrigin,
		canReuse = true,
		reuseLabel = 'Reuse settings',
		onReuse
	}: {
		live: boolean;
		cancelling?: boolean;
		onCancel: () => void;
		origin?: OriginAction | null;
		onOrigin: (action: OriginAction) => void;
		canReuse?: boolean;
		reuseLabel?: string;
		onReuse: () => void;
	} = $props();

	let reason = $derived(live ? 'Available when the generation finishes' : 'Needs a finished picture');
	let disabledActions = $derived(
		live
			? [
					{ id: 'edit', label: 'Edit', icon: 'brush' },
					{ id: 'effects', label: 'Effects', icon: 'sparkles' },
					{ id: 'download', label: 'Download', icon: 'download' },
					{ id: 'more', label: 'More', icon: 'more' }
				]
			: [
					{ id: 'edit', label: 'Edit', icon: 'brush' },
					{ id: 'download', label: 'Download', icon: 'download' },
					{ id: 'more', label: 'More', icon: 'more' }
				]
	);
	let originLabel = $derived(
		origin?.kind === 'open-in-tab' ? OPEN_IN_TAB_LABEL : origin?.kind === 'go-to-tab' ? GO_TO_TAB_LABEL : OPEN_EDIT_LABEL
	);
	let originIcon = $derived(origin?.kind === 'open-edit' ? 'brush' : 'external-link');
</script>

<div
	class="flex h-11 min-w-0 shrink-0 items-center gap-1 border-b border-line bg-surface-1 px-1.5 max-sm:h-12"
	role="toolbar"
	aria-label="Actions"
	data-live-action-bar
>
	{#if live}
		<Button
			variant="ghost"
			size="sm"
			icon="close"
			loading={cancelling}
			class="!text-danger hover:!text-danger"
			attrs={{ 'data-live-action': 'cancel' }}
			onclick={onCancel}
		>
			Cancel
		</Button>
	{/if}
	{#if origin}
		<Button
			variant="secondary"
			size="sm"
			icon={originIcon}
			attrs={{ 'data-live-action': origin.kind }}
			onclick={() => origin && onOrigin(origin)}
		>
			{originLabel}
		</Button>
	{/if}
	{#if canReuse}
		<Button variant="ghost" size="sm" icon="refresh" attrs={{ 'data-live-action': 'reuse' }} onclick={onReuse}>
			{reuseLabel}
		</Button>
	{/if}
	<span class="ml-auto flex items-center gap-1 max-sm:hidden">
		{#each disabledActions as action (action.id)}
			<span use:tooltip={reason} class="inline-flex" data-live-disabled={action.id}>
				<Button variant="ghost" size="sm" icon={action.icon} disabled>{action.label}</Button>
			</span>
		{/each}
	</span>
</div>
