<script lang="ts">
	import { untrack } from 'svelte';
	import { Button } from '$lib/components/ui';
	import type { EditorToolProps } from '$lib/editors/railTools';
	import { appliedSteps, effectiveSteps } from '$lib/tool-tabs/frame/editStack';
	import { outputSize } from '$lib/tool-tabs/image/payload';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { leftOutCount } from '$lib/tool-tabs/image/generate/generateStep';
	import { UPSCALE_FACTORS, UPSCALE_TASK, targetFromHeight, targetFromWidth, upscaleReasonText, upscaleSize, type RunOptions } from '$lib/tool-tabs/image/generate/upscale';
	import type { ImageToolHost } from '$lib/tool-tabs/image/toolHost';

	let { host, phone = false }: EditorToolProps = $props();

	const generate = getGenerateController();
	const ui = generate?.ui;
	const stack = untrack(() => (host as ImageToolHost).stack);

	let source = $derived(outputSize(effectiveSteps($stack), host.source.size));
	let options = $derived<RunOptions>($ui?.options[UPSCALE_TASK] ?? { scale: UPSCALE_FACTORS[0] });
	let outcome = $derived(source ? upscaleSize(source, options) : null);
	let chip = $derived.by(() => {
		void $ui;
		return generate ? generate.chipFor(UPSCALE_TASK) : null;
	});
	let working = $derived(($ui?.starting ?? false) || leftOutCount(appliedSteps($stack)) > 0);
	let width = $state('');
	let height = $state('');

	$effect(() => {
		if (options.scale && outcome?.ok) {
			width = String(outcome.size.width);
			height = String(outcome.size.height);
		}
	});

	function pickScale(scale: number) {
		generate?.setOptions(UPSCALE_TASK, { scale });
	}

	function typed(side: 'width' | 'height', text: string) {
		if (side === 'width') width = text;
		else height = text;
		const value = Math.round(Number(text));
		if (!source || !Number.isFinite(value) || value < 1) return;
		const target = side === 'width' ? targetFromWidth(source, value) : targetFromHeight(source, value);
		if (side === 'width') height = String(target.height);
		else width = String(target.width);
		generate?.setOptions(UPSCALE_TASK, { target });
	}

	function run() {
		if (generate && chip && outcome?.ok && !working) void generate.generate(UPSCALE_TASK, false);
	}
</script>

<div class="flex flex-col gap-4" data-upscale-options>
	<div class="flex items-center justify-between text-sm">
		<span class="text-fg-muted">Picture</span>
		<span class="font-mono tabular-nums text-fg" data-upscale-source>{source ? `${source.width}×${source.height}` : '–'}</span>
	</div>

	<div class="flex flex-col gap-1.5">
		<span class="text-sm text-fg-muted" id="upscale-factor-label">Enlarge by</span>
		<div class="grid grid-cols-2 gap-1.5" role="group" aria-labelledby="upscale-factor-label">
			{#each UPSCALE_FACTORS as factor (factor)}
				<Button size="sm" class="font-mono tabular-nums" pressed={options.scale === factor} onclick={() => pickScale(factor)} attrs={{ 'data-upscale-factor': factor }}>{factor}×</Button>
			{/each}
		</div>
	</div>

	<div class="flex flex-col gap-1.5">
		<span class="text-sm text-fg-muted" id="upscale-size-label">Or set a size</span>
		<div class="flex items-center gap-2" role="group" aria-labelledby="upscale-size-label">
			<input
				class="input h-8 min-w-0 flex-1 font-mono tabular-nums"
				type="number"
				inputmode="numeric"
				min="1"
				aria-label="Width"
				value={width}
				oninput={(event) => typed('width', event.currentTarget.value)}
				data-upscale-width
			/>
			<span class="flex-none text-fg-subtle" aria-hidden="true">×</span>
			<input
				class="input h-8 min-w-0 flex-1 font-mono tabular-nums"
				type="number"
				inputmode="numeric"
				min="1"
				aria-label="Height"
				value={height}
				oninput={(event) => typed('height', event.currentTarget.value)}
				data-upscale-height
			/>
		</div>
		<p class="text-sm text-fg-subtle">The shape of the picture is kept.</p>
	</div>

	<div class="flex items-center justify-between text-sm">
		<span class="text-fg-muted">Result</span>
		{#if outcome?.ok}
			<span class="font-mono tabular-nums text-success" data-upscale-result>{outcome.size.width}×{outcome.size.height}</span>
		{:else}
			<span class="font-mono tabular-nums text-fg-subtle" data-upscale-result>–</span>
		{/if}
	</div>
	{#if outcome && !outcome.ok}
		<p class="text-sm text-warning" role="alert" data-upscale-reason>{upscaleReasonText(outcome.reason)}</p>
	{/if}

	{#if !phone}
		<Button variant="primary" size="md" class="w-full" disabled={!chip || !outcome?.ok || working} loading={working} onclick={run} attrs={{ 'data-upscale-run': '' }}>Upscale</Button>
		{#if !chip}
			<p class="text-sm text-fg-subtle">Choose a setup in the bar below first.</p>
		{/if}
	{/if}
</div>
