<script lang="ts">
	import { Button } from '$lib/components/ui';
	import type { PaintSession, SessionSnapshot } from './session';

	export let session: PaintSession;
	export let state: SessionSnapshot;

	$: layer = state.layers[state.activeIndex];
	$: percent = layer?.source ? Math.round((layer.canvas.width / layer.source.width) * 100) : 100;

	function number(event: Event): number {
		return Number((event.currentTarget as HTMLInputElement).value);
	}
</script>

<div class="flex flex-col gap-3 p-3">
	<p class="font-mono text-xs uppercase tracking-[0.08em] text-fg-subtle">Transform</p>
	<div class="flex items-center gap-2">
		<label for="transform-scale" class="w-16 shrink-0 text-xs text-fg-muted">Scale</label>
		<input
			id="transform-scale"
			type="range"
			min="5"
			max="400"
			value={percent}
			class="flex-1 min-w-0 h-6 accent-signal"
			on:input={(event) => session.scaleActiveLayer(number(event), false)}
			on:change={(event) => session.scaleActiveLayer(number(event), true)}
		/>
		<span class="w-12 text-right font-mono text-xs tabular-nums text-fg-muted">{percent}%</span>
	</div>
	<div class="flex items-center gap-2">
		<Button variant="secondary" size="sm" onclick={() => session.fitActiveLayer()}>Fit to canvas</Button>
		<Button variant="secondary" size="sm" onclick={() => session.scaleActiveLayer(100, true)}>100%</Button>
	</div>
	<p class="font-mono text-xs tabular-nums text-fg-muted">
		{layer?.name ?? ''} · {layer?.canvas.width ?? 0} × {layer?.canvas.height ?? 0}
	</p>
	<p class="text-xs leading-relaxed text-fg-subtle">
		Drag the layer to move it. Drag a corner handle to scale it. Use the slider for exact sizes.
	</p>
</div>
