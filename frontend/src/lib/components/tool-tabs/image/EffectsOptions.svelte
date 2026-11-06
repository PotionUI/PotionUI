<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import ToolForm from '$lib/media-tools/ToolForm.svelte';
	import { effectDefaults, effectGroups, type EffectTool } from '$lib/effects/catalog';
	import PropertySlider from '$lib/components/tool-tabs/frame/PropertySlider.svelte';
	import { filterCatalog, loadFilterCatalog } from '$lib/filters/catalog';
	import { getOp } from '$lib/filters/engine';
	import { sharedExtensions } from '$lib/filters/extensions';
	import { paintFilters } from '$lib/components/imageEditor/registries';
	import { stillShader } from '$lib/tool-tabs/image/shaderEffect';
	import type { EffectParams, LookParams, ToolEffectParams } from '$lib/tool-tabs/image/steps';

	let {
		params,
		phone = false,
		effects = [],
		query = $bindable(''),
		onopen,
		onchange
	}: {
		params: EffectParams | null;
		phone?: boolean;
		effects?: EffectTool[];
		query?: string;
		onopen?: (tool: string) => boolean;
		onchange: (next: EffectParams) => void;
	} = $props();

	onMount(() => {
		void loadFilterCatalog();
	});

	onDestroy(() => stillShader.dispose());

	let extensions = $derived(sharedExtensions($filterCatalog.ops, paintFilters.list()));
	let look = $derived(params?.kind === 'look' ? params : null);
	let chosen = $derived(params?.kind === 'effect' ? params : null);
	let item = $derived(look ? ($filterCatalog.items.find((entry) => entry.id === look.filter) ?? null) : null);
	let contains = $derived(
		[...new Set((item?.steps ?? []).map((step) => getOp(step.op, extensions)?.label).filter((label): label is string => !!label))]
	);
	let groups = $derived(effectGroups(effects));
	let chosenEffect = $derived(chosen ? (effects.find((effect) => effect.id === chosen.tool) ?? null) : null);

	function pickEffect(effect: EffectTool) {
		if (chosen?.tool === effect.id) return;
		if (onopen?.(effect.id)) return;
		onchange({ kind: 'effect', tool: effect.id, name: effect.label, values: effectDefaults(effect) } satisfies ToolEffectParams);
	}

	function setEffectValue(name: string, value: unknown) {
		if (!chosen) return;
		onchange({ ...chosen, values: { ...chosen.values, [name]: value } });
	}

	function setIntensity(value: number) {
		if (look) onchange({ ...look, intensity: value } satisfies LookParams);
	}
</script>

{#snippet effectBody(value: ToolEffectParams, effect: EffectTool)}
	{#if effect.description}
		<p class="text-sm text-fg-muted" data-effect-description>{effect.description}</p>
	{/if}
	{#if effect.params.length}
		<ToolForm params={effect.params} values={value.values} onChange={setEffectValue} />
	{/if}
{/snippet}

{#if !phone}
	<input
		type="search"
		class="h-8 w-full rounded border border-line-strong bg-surface-2 px-2.5 text-base text-fg placeholder:text-fg-subtle focus:border-signal/60 focus:outline-none"
		placeholder="Search looks"
		aria-label="Search looks"
		bind:value={query}
	/>
{/if}
{#if look}
	<div class="flex flex-col gap-3" data-look-properties>
		<PropertySlider label="Intensity" value={look.intensity} min={0} max={100} defaultValue={item?.intensity ?? 100} onchange={setIntensity} />
		{#if contains.length}
			<div class="flex flex-col gap-1.5">
				<p class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{look.name} contains</p>
				<div class="flex flex-wrap gap-1.5" data-look-contains>
					{#each contains as label (label)}
						<span class="inline-flex h-6 items-center rounded bg-surface-3 px-2 font-mono text-sm text-fg-muted">{label}</span>
					{/each}
				</div>
			</div>
		{/if}
	</div>
{:else if !chosen}
	<p class="text-sm text-fg-muted" data-looks-hint>Hover a look in the tray to preview it, click to choose it, then Apply.</p>
{/if}

{#if effects.length}
	<div class="flex flex-col gap-3 {look ? 'border-t border-line pt-4' : ''}" data-effects-list>
		<p class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">Effects</p>
		{#if phone}
			<div class="-mx-1 flex gap-1.5 overflow-x-auto px-1 pb-0.5" role="listbox" aria-label="Effects" data-effect-strip>
				{#each effects as effect (effect.id)}
					<button
						type="button"
						role="option"
						aria-selected={chosen?.tool === effect.id}
						class="h-8 flex-none rounded border px-2.5 text-sm {chosen?.tool === effect.id
							? 'border-signal/60 bg-signal/10 text-signal'
							: 'border-line-strong bg-surface-2 text-fg-muted hover:border-line-hover hover:text-fg'}"
						onclick={() => pickEffect(effect)}
						data-effect-chip={effect.id}>{effect.label}</button
					>
				{/each}
			</div>
			{#if chosen && chosenEffect}
				{@render effectBody(chosen, chosenEffect)}
			{/if}
		{:else}
			{#each groups as group (group.id)}
				<div class="flex flex-col gap-1.5" data-effect-group={group.id}>
					<span class="font-mono text-sm uppercase tracking-[0.08em] text-fg-subtle">{group.label}</span>
					{#each group.effects as effect (effect.id)}
						{@const open = chosen?.tool === effect.id}
						<div class="rounded-lg border {open ? 'border-signal/50 bg-surface-2' : 'border-line bg-surface-1 hover:border-line-hover'}" data-effect-card={effect.id} data-open={open || undefined}>
							<button type="button" class="flex h-10 w-full items-center gap-2 px-3 text-left text-base text-fg" aria-expanded={open} onclick={() => pickEffect(effect)}>
								<Icon name={effect.icon} className="h-3.5 w-3.5 flex-none text-fg-muted" />
								<span class="min-w-0 flex-1 truncate">{effect.label}</span>
							</button>
							{#if open && chosen}
								<div class="flex flex-col gap-3 px-3 pb-3">
									{@render effectBody(chosen, effect)}
								</div>
							{/if}
						</div>
					{/each}
				</div>
			{/each}
		{/if}
	</div>
{/if}
