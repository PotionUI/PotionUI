<script lang="ts">
	import ToolForm from '$lib/media-tools/ToolForm.svelte';
	import { effectDefaults } from '$lib/effects/catalog';
	import { mediaToolCatalog } from '$lib/media-tools/catalog';
	import type { PendingEdit, StepDraft } from '$lib/tool-tabs/frame/editStack';
	import { imageEffects } from '$lib/tool-tabs/image/shaderEffect';
	import { toolEffectDraft, type EffectParams, type ToolEffectParams } from '$lib/tool-tabs/image/steps';
	import type { ImageToolHost } from '$lib/tool-tabs/image/toolHost';

	let { host, tool }: { host: unknown; phone?: boolean; tool: string } = $props();

	let image = $derived(host as ImageToolHost);
	let pendingNow = $state.raw<PendingEdit<unknown> | null>(null);

	$effect(() => image.pending.subscribe((next) => (pendingNow = next)));

	let fx = $derived(imageEffects($mediaToolCatalog).find((candidate) => candidate.id === tool) ?? null);
	let held = $derived.by(() => {
		const params = pendingNow?.params as EffectParams | undefined;
		return params?.kind === 'effect' && params.tool === tool ? params : null;
	});
	let values = $derived<Record<string, unknown>>(held ? held.values : fx ? effectDefaults(fx) : {});

	function change(name: string, value: unknown) {
		if (!fx) return;
		const params: ToolEffectParams = { kind: 'effect', tool, name: fx.label, values: { ...values, [name]: value } };
		const draft = toolEffectDraft(params, $mediaToolCatalog) as StepDraft<unknown>;
		if (held) image.update(draft);
		else image.begin(draft);
	}
</script>

<div class="flex flex-col gap-3" data-effect-card-options={tool}>
	{#if fx}
		{#if fx.description}
			<p class="text-sm text-fg-muted" data-effect-description>{fx.description}</p>
		{/if}
		{#if fx.params.length}
			<ToolForm params={fx.params} {values} onChange={change} />
		{/if}
	{:else}
		<p class="text-sm text-fg-muted">This effect is not available right now.</p>
	{/if}
</div>
