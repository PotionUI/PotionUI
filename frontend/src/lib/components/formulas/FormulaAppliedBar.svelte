<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, IconButton } from '$lib/components/ui';
	import { appliedKey, undoApplied } from '$lib/formulas/planApply';
	import { formulaApplied } from '$lib/stores/formulaApplied';
	import { tabsStore } from '$lib/stores/tabs';
	import type { Tab } from '$lib/types/tabs';

	let { tab, compact = false }: { tab: Tab; compact?: boolean } = $props();

	let applied = $derived($formulaApplied[tab.id]);
	let changedCount = $derived(applied ? Object.keys(applied.changed).length : 0);

	let currentKey = $derived(appliedKey(tab));

	$effect(() => {
		if (applied && applied.key !== currentKey) formulaApplied.clear(tab.id);
	});

	function undo() {
		if (!applied || applied.key !== currentKey) return;
		tabsStore.updateTab(tab.id, { formData: undoApplied(tab.formData, applied.snapshot) });
		formulaApplied.clear(tab.id);
	}
</script>

{#if applied && applied.key === currentKey}
	<div
		class="flex items-center gap-3 rounded-lg border border-signal/40 bg-signal/10 {compact ? 'mx-3 mb-2 mt-1 px-3 py-2' : 'mb-3 px-3 py-2.5'}"
		role="status"
		data-testid="formula-applied-bar"
	>
		<Icon name="check" className="h-4 w-4 flex-shrink-0 text-signal" />
		<div class="min-w-0 flex-1">
			<div class="truncate text-sm text-fg"><span class="font-semibold">{applied.name}</span> applied</div>
			<div class="font-mono text-xs tabular-nums text-fg-muted">
				{changedCount} changed{#if applied.skipped > 0}{' · '}{applied.skipped} skipped{/if}
			</div>
		</div>
		<Button variant="secondary" size="sm" icon="undo" onclick={undo}>Undo</Button>
		<Tooltip text="Dismiss" position="bottom" delay={150}>
			<IconButton icon="close" label="Dismiss applied formula" size="sm" onclick={() => formulaApplied.clear(tab.id)} />
		</Tooltip>
	</div>
{/if}
