<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import PaintLayers from '$lib/components/paint/PaintLayers.svelte';
	import { paintIconName } from '$lib/paint/entries';
	import type { LayerAction } from '$lib/paint/layerActions';
	import type { PaintSession, SessionSnapshot } from '$lib/paint/session';

	let {
		session,
		state: snapshot,
		phone = false,
		index = 0,
		actions = [],
		rowShown = false
	}: {
		session: PaintSession;
		state: SessionSnapshot;
		phone?: boolean;
		index?: number;
		actions?: LayerAction[];
		rowShown?: boolean;
	} = $props();

	let count = $derived(snapshot.layers.filter((layer) => !layer.internal).length);
</script>

{#if rowShown}
	{#if count > 2}
		<div class="flex flex-col gap-1.5 pb-1" data-paint-group="attached">
			<PaintLayers {session} state={snapshot} {phone} {actions} nested />
		</div>
	{/if}
{:else}
<div class="flex flex-col gap-1.5 pb-1" data-paint-group>
	<div class="relative flex h-11 items-center gap-2 rounded bg-signal/[0.08] pl-1 pr-1.5 before:absolute before:-left-2 before:inset-y-2 before:w-0.5 before:bg-signal" data-stack-group-header>
		<span class="w-3 flex-none" aria-hidden="true"></span>
		<span class="w-[18px] flex-none font-mono text-sm tabular-nums text-fg-subtle">{String(index).padStart(2, '0')}</span>
		<span class="grid h-7 w-7 flex-none place-items-center rounded bg-surface-2 text-fg-muted">
			<Icon name={paintIconName('brush')} className="h-3.5 w-3.5" />
		</span>
		<span class="flex min-w-0 flex-1 flex-col">
			<span class="truncate text-base font-medium leading-[18px] text-fg">Paint</span>
			<span class="truncate text-sm text-signal" data-stack-state="in-progress">In progress<span class="whitespace-pre text-fg-subtle">{' · '}</span><span class="font-mono tabular-nums text-fg-subtle" data-layer-count>{count}</span><span class="whitespace-pre text-fg-subtle">{` ${count === 1 ? 'layer' : 'layers'}`}</span></span>
		</span>
	</div>
	<PaintLayers {session} state={snapshot} {phone} {actions} nested />
</div>
{/if}
