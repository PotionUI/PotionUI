<script lang="ts">
	import { onDestroy } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import FormulasDrawer from './FormulasDrawer.svelte';
	import { formulaLists, formulasKey, getFormulaDeclaration, loadFormulas } from '$lib/stores/formulas';
	import { sideDrawer } from '$lib/stores/sideDrawer';
	import type { FormulaDeclaration } from '$lib/formulas/types';
	import type { GenerationTab } from '$lib/types/tabs';

	let {
		tab,
		presetName,
		presetVersion,
		modeLabel,
		onApplied,
		layout
	}: {
		tab: GenerationTab;
		presetName: string;
		presetVersion: string;
		modeLabel: string;
		onApplied?: () => void;
		layout: 'rail' | 'sheet';
	} = $props();

	let loaded = $state.raw<{ key: string; value: FormulaDeclaration | null } | null>(null);
	let open = $state(false);

	const presetId = $derived(tab.selectedPreset ?? '');
	const mode = $derived(tab.selectedMode ?? '');
	const variant = $derived(tab.selectedVariant ?? null);
	const declarationKey = $derived(`${presetId} ${mode} ${variant ?? ''}`);
	const declaration = $derived(loaded && loaded.key === declarationKey ? loaded.value : null);
	const count = $derived($formulaLists[formulasKey(presetId, mode)]?.items.length ?? 0);

	$effect(() => {
		const key = declarationKey;
		const id = presetId;
		const currentMode = mode;
		if (!id || !currentMode) return;
		let live = true;
		getFormulaDeclaration(id, currentMode, variant).then((block) => {
			if (!live) return;
			loaded = { key, value: block };
			if (block) void loadFormulas(id, currentMode);
		});
		return () => {
			live = false;
		};
	});

	$effect(() => {
		if ($sideDrawer !== 'formulas') open = false;
	});

	$effect(() => {
		if (!declaration) open = false;
	});

	function toggle() {
		if (open) {
			open = false;
			sideDrawer.close('formulas');
			return;
		}
		sideDrawer.open('formulas');
		open = true;
	}

	function close() {
		open = false;
		sideDrawer.close('formulas');
	}

	onDestroy(() => sideDrawer.close('formulas'));
</script>

{#if declaration}
	<button
		type="button"
		class="flex flex-shrink-0 items-center justify-center gap-1.5 rounded text-sm transition-colors {layout === 'rail'
			? `h-8 border px-2.5 ${open ? 'border-line-hover bg-control-fill-hover text-fg' : 'border-line bg-control-fill text-fg hover:border-line-hover hover:bg-control-fill-hover'}`
			: `h-[42px] flex-1 border px-3 ${open ? 'border-line-hover bg-surface-3 text-fg' : 'border-line bg-surface-2 text-fg hover:border-line-hover hover:bg-surface-3'}`}"
		aria-label="Formulas"
		aria-haspopup="dialog"
		aria-expanded={open}
		data-testid="formulas-button"
		onclick={toggle}
	>
		<Icon name="book" className="h-4 w-4 flex-shrink-0 {layout === 'rail' ? '' : 'text-fg-muted'}" />
		<span>Formulas</span>
		{#if count > 0}
			<span class="font-mono text-sm tabular-nums text-fg-subtle" data-testid="formulas-count">{count}</span>
		{/if}
	</button>

	{#if open}
		<FormulasDrawer {tab} {declaration} {presetName} {presetVersion} {modeLabel} onClose={close} {onApplied} />
	{/if}
{/if}
