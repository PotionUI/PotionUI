<script lang="ts">
	import { Button } from '$lib/components/ui';
	import { detailFooterState } from './detailFooter';

	let {
		dirtyCount = 0,
		mode = 'edit',
		saving = false,
		canSave = true,
		onSave,
		onDiscard
	}: {
		dirtyCount?: number;
		mode?: 'edit' | 'create';
		saving?: boolean;
		canSave?: boolean;
		onSave: () => void;
		onDiscard: () => void;
	} = $props();

	const state = $derived(detailFooterState({ dirtyCount, mode, saving, canSave }));
</script>

<div
	class="flex-shrink-0 border-t border-line bg-surface-1 px-4 sm:px-5 py-2.5 flex items-center justify-end gap-2"
	data-detail-footer
>
	{#if state.dirtyLabel}
		<span class="mr-auto flex items-center gap-1.5 font-mono text-xs text-warning">
			<span class="w-1.5 h-1.5 rounded-full bg-warning"></span>
			{state.dirtyLabel}
		</span>
	{/if}
	<Button size="sm" variant="secondary" disabled={state.discardDisabled} onclick={onDiscard}>
		{state.discardLabel}
	</Button>
	<Button size="sm" variant="primary" loading={saving} disabled={state.saveDisabled} onclick={onSave}>
		{state.saveLabel}
	</Button>
</div>
