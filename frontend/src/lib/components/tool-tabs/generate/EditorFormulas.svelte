<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button } from '$lib/components/ui';
	import FormulasButton from '$lib/components/formulas/FormulasButton.svelte';
	import { detachedFormTab } from '$lib/form/detachedFormTab';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { choiceKey, modeName, offerOf } from '$lib/tool-tabs/image/generate/setup';

	let { taskId, phone = false }: { taskId: string; phone?: boolean } = $props();

	const generate = getGenerateController()!;
	const ui = generate.ui;

	let task = $derived($ui.tasks.find((entry) => entry.id === taskId) ?? null);
	let choice = $derived($ui.choice[taskId] ?? null);
	let offer = $derived(task && choice ? offerOf(task.offers, choice) : null);
	let key = $derived(choice ? choiceKey(choice) : null);
	let values = $derived($ui.tweaks[taskId]?.values);
	let applied = $derived($ui.formulas[taskId] ?? []);
	let version = $derived(offer ? ($ui.catalog.find((entry) => entry.id === offer.preset_id)?.version ?? '') : '');

	$effect(() => {
		void key;
		if (taskId && key && !$ui.tweaks[taskId]) void generate.settings(taskId);
	});

	let tab = $derived(
		offer && key && values
			? detachedFormTab({ id: `editor-settings:${key}`, presetId: offer.preset_id, mode: offer.mode, formData: values })
			: null
	);
</script>

{#if !tab || !offer}
	<span class="dock-btn">
		<Tooltip text="Choose a preset and a mode first" position="top">
			<span class="inline-flex">
				<Button size="sm" icon="book" class="h-8 text-base" disabled ariaLabel="Formulas" attrs={{ 'data-testid': 'formulas-button', 'aria-disabled': 'true', 'aria-describedby': 'dock-setup-hint-formulas' }}>Formulas</Button>
				<span id="dock-setup-hint-formulas" class="sr-only">Choose a preset and a mode first</span>
			</span>
		</Tooltip>
	</span>
{:else}
	<FormulasButton
		{tab}
		layout="dock"
		presetName={offer.preset_name}
		presetVersion={version}
		modeLabel={modeName(offer)}
		host={generate.formulaHost(taskId)}
		appliedCount={applied.length}
	/>
{/if}
