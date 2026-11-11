<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { Switch } from '$lib/components/ui';
	import { SCOPE_DEFS, type ScopeRowState } from './apiKeyDialog';

	let {
		row,
		onToggle
	}: {
		row: ScopeRowState;
		onToggle: (on: boolean) => void;
	} = $props();

	const def = $derived(SCOPE_DEFS[row.scope]);
</script>

<div class="flex items-start gap-3 py-3">
	<div class="min-w-0 flex-1">
		<div class="flex flex-wrap items-baseline gap-x-2">
			<span class="text-sm font-medium text-fg">{def.label}</span>
			<code class="font-mono text-sm text-fg-subtle">{def.id}</code>
		</div>
		<p class="mt-0.5 text-sm text-fg-subtle">{row.description}</p>
		{#if row.lockNote}
			<p class="mt-1 flex items-start gap-1.5 text-sm text-fg-subtle">
				<Icon name="lock" className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
				<span>{row.lockNote}</span>
			</p>
		{/if}
	</div>
	<div class="pt-0.5">
		<Switch checked={row.on} disabled={row.disabled} label={def.label} onchange={onToggle} />
	</div>
</div>
