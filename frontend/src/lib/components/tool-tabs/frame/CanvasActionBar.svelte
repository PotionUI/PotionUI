<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import ColourControl from '$lib/components/paint/ColourControl.svelte';
	import { Button, IconButton, Kbd } from '$lib/components/ui';
	import type { CanvasAction } from '$lib/editors/railTools';
	import EditorEntryComponent from './EditorEntryComponent.svelte';

	let {
		actions,
		hidden = false,
		variant = 'floating'
	}: {
		actions: CanvasAction[];
		hidden?: boolean;
		variant?: 'floating' | 'strip';
	} = $props();

	interface Cluster {
		key: string;
		primary: boolean;
		segmented: boolean;
		items: CanvasAction[];
	}

	let clusters = $derived.by(() => {
		const out: Cluster[] = [];
		for (const action of actions) {
			const key = action.primary ? `primary:${action.id}` : (action.group ?? action.id);
			const last = out[out.length - 1];
			if (last && last.key === key) last.items.push(action);
			else out.push({ key, primary: !!action.primary, segmented: false, items: [action] });
		}
		for (const cluster of out) cluster.segmented = cluster.items.length > 1 && cluster.items.every((item) => item.kind === 'toggle');
		return out;
	});

	let strip = $derived(variant === 'strip');

	function reasonOf(action: CanvasAction): string {
		return action.disabled && action.reason ? action.reason : '';
	}

	function percent(action: CanvasAction): number {
		const value = action.value;
		if (!value || typeof value.value !== 'number') return 0;
		const min = value.min ?? 0;
		const max = value.max ?? 100;
		return max > min ? Math.min(100, Math.max(0, ((value.value - min) / (max - min)) * 100)) : 0;
	}
</script>

{#snippet item(action: CanvasAction)}
	{#if action.kind === 'custom' && action.component}
		<EditorEntryComponent entry="canvas-action:{action.id}" componentRef={action.component} props={action.props ?? {}} />
	{:else if action.kind === 'value' && action.value?.type === 'range'}
		{@const value = action.value}
		<label class="inline-flex flex-none items-center gap-2 px-1.5 text-sm text-fg-muted" data-canvas-action={action.id} data-canvas-kind="value">
			<span class="cab-label">{action.label}</span>
			<input
				type="range"
				class="cab-range h-1.5 w-24 cursor-pointer appearance-none rounded-full"
				min={value.min}
				max={value.max}
				step={value.step}
				value={value.value}
				aria-label={action.label}
				style="background: linear-gradient(to right, rgb(var(--signal)) {percent(action)}%, rgb(var(--surface-3)) {percent(action)}%)"
				oninput={(event) => value.set(Number((event.currentTarget as HTMLInputElement).value))}
			/>
			<span class="min-w-[3.5rem] text-right font-mono tabular-nums text-fg">{value.value}{value.unit ? ` ${value.unit}` : ''}</span>
		</label>
	{:else if action.kind === 'value' && action.value?.type === 'colour'}
		{@const value = action.value}
		<span class="cab-colour inline-flex flex-none items-center px-1.5" data-canvas-action={action.id} data-canvas-kind="value">
			<ColourControl color={String(value.value)} onchange={(hex) => value.set(hex)} />
		</span>
	{:else if action.kind === 'toggle' || action.iconOnly}
		<IconButton
			icon={action.icon ?? 'extension'}
			label={action.label}
			size="sm"
			active={action.kind === 'toggle' && action.pressed}
			ariaPressed={action.kind === 'toggle' ? !!action.pressed : undefined}
			disabled={action.disabled}
			tooltip={reasonOf(action) ? `${action.label}: ${reasonOf(action)}` : action.label}
			kbd={action.shortcut}
			onclick={() => action.run?.()}
			data-canvas-action={action.id}
			data-canvas-kind={action.kind}
		/>
	{:else if strip}
		<button
			type="button"
			class="flex min-w-0 flex-col items-center justify-center gap-1 rounded px-1 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 {action.primary ? 'flex-[1.3] bg-accent text-accent-contrast' : 'flex-1 text-fg-muted hover:text-fg'}"
			disabled={action.disabled}
			aria-label={action.label}
			onclick={() => action.run?.()}
			data-canvas-action={action.id}
			data-canvas-kind="action"
		>
			{#if action.icon}<Icon name={action.icon} className="h-4 w-4" />{/if}
			<span class="whitespace-nowrap">{action.shortLabel ?? action.label}</span>
		</button>
	{:else}
		<Tooltip text={action.label} detail={reasonOf(action)} kbd={action.shortcut}>
			<Button
				variant={action.primary ? 'primary' : 'ghost'}
				size="sm"
				class="h-8 {action.primary ? 'font-semibold' : ''}"
				icon={action.icon}
				disabled={action.disabled}
				ariaLabel={action.label}
				onclick={() => action.run?.()}
				attrs={{ 'data-canvas-action': action.id, 'data-canvas-kind': 'action' }}
			>
				<span class="cab-label">{action.label}</span>
				{#if action.shortcut && !action.primary}<span class="cab-label"><Kbd keys={action.shortcut} /></span>{/if}
			</Button>
		</Tooltip>
	{/if}
{/snippet}

{#snippet clustered()}
	{#each clusters as cluster, index (cluster.key)}
		{#if index > 0 && !cluster.primary && !strip}
			<span class="mx-0.5 h-5 w-px flex-none bg-line-strong" aria-hidden="true"></span>
		{/if}
		{#if cluster.segmented}
			<div class="inline-flex flex-none items-center gap-0.5 rounded bg-surface-1 p-0.5" role="group" aria-label={cluster.key} data-canvas-cluster={cluster.key}>
				{#each cluster.items as action (action.id)}{@render item(action)}{/each}
			</div>
		{:else}
			{#each cluster.items as action (action.id)}{@render item(action)}{/each}
		{/if}
	{/each}
{/snippet}

{#if actions.length}
	{#if strip}
		<div
			class="flex flex-none items-center gap-1 overflow-x-auto border-t border-line bg-surface-1 px-2 py-1.5 {hidden ? 'invisible' : ''}"
			role="toolbar"
			aria-label="Canvas actions"
			data-canvas-actions="strip"
			data-canvas-actions-hidden={hidden ? '' : undefined}
		>
			{@render clustered()}
		</div>
	{:else if !hidden}
		<div class="cab-host pointer-events-none absolute inset-x-3 bottom-[58px] z-10 flex justify-center" data-canvas-actions-host>
			<div
				class="cab pointer-events-auto flex max-w-full items-center gap-1 overflow-x-auto rounded-xl border border-line-strong bg-surface-2 p-1 shadow-floating"
				role="toolbar"
				aria-label="Canvas actions"
				data-canvas-actions="floating"
			>
				{@render clustered()}
			</div>
		</div>
	{/if}
{/if}

<style>
	.cab-host {
		container-type: inline-size;
	}

	@container (max-width: 760px) {
		.cab :global(.cab-label) {
			display: none;
		}
	}

	.cab :global([data-colour-recent]),
	[data-canvas-actions='strip'] :global([data-colour-recent]) {
		display: none;
	}

	.cab-range::-webkit-slider-thumb {
		appearance: none;
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: rgb(var(--fg));
		cursor: pointer;
	}

	.cab-range::-moz-range-thumb {
		width: 14px;
		height: 14px;
		border-radius: 50%;
		background: rgb(var(--fg));
		cursor: pointer;
		border: none;
	}
</style>
