<script lang="ts">
	import { onDestroy } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import FormulasDrawer from './FormulasDrawer.svelte';
	import { formulaLists, formulasKey, getFormulaDeclaration, loadFormulas } from '$lib/stores/formulas';
	import { sideDrawer } from '$lib/stores/sideDrawer';
	import type { FormulaDeclaration } from '$lib/formulas/types';
	import type { Tab } from '$lib/types/tabs';

	let {
		tab,
		presetName,
		presetVersion,
		modeLabel,
		onApplied,
		grouped = false
	}: {
		tab: Tab;
		presetName: string;
		presetVersion: string;
		modeLabel: string;
		onApplied?: () => void;
		grouped?: boolean;
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
	<Tooltip text="Formulas" position="bottom" delay={150} wrapperClass={grouped ? 'flex h-full' : undefined}>
		<button
			type="button"
			class="relative flex flex-shrink-0 items-center justify-center text-fg-muted transition-colors hover:bg-surface-3 hover:text-fg {grouped ? 'h-full w-7' : 'h-9 w-9 rounded border border-line-strong bg-surface-2 hover:border-line-hover'}"
			aria-label="Formulas"
			aria-haspopup="dialog"
			aria-expanded={open}
			data-testid="formulas-button"
			onclick={toggle}
		>
			<Icon name="book" className="h-4 w-4" />
			{#if count > 0}
				<span class="absolute {grouped ? 'right-0 top-0' : '-right-1 -top-1'} inline-flex h-4 min-w-[16px] items-center justify-center rounded border border-line-strong bg-surface-3 px-1 font-mono text-2xs tabular-nums text-fg">{count}</span>
			{/if}
		</button>
	</Tooltip>

	{#if open}
		<FormulasDrawer {tab} {declaration} {presetName} {presetVersion} {modeLabel} onClose={close} {onApplied} />
	{/if}
{/if}
