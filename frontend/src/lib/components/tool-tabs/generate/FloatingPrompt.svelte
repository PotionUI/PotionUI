<script lang="ts">
	import { onDestroy } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, IconButton, Kbd, SegmentedControl } from '$lib/components/ui';
	import { tasks } from '$lib/editors/tasks';
	import { editorsRevision } from '$lib/editors/registry';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { areaLabel } from '$lib/tool-tabs/image/generate/area';
	import { CHOOSE_SETUP } from '$lib/tool-tabs/image/generate/controller';
	import { maskArea } from '$lib/tool-tabs/image/maskOverlays';
	import SetupPicker from './SetupPicker.svelte';

	let {
		phone = false,
		running = null,
		large = false,
		settings,
		onclose
	}: {
		phone?: boolean;
		running?: string | null;
		large?: boolean;
		settings?: { open: boolean; toggle: () => void };
		onclose?: () => void;
	} = $props();

	const generate = getGenerateController()!;
	const ui = generate.ui;
	const POSITION_KEY = 'potionui_editor_prompt_position';
	const RESULT_ITEMS = [1, 2, 3, 4].map((count) => ({ id: String(count), label: String(count) }));

	function readPosition(): { x: number; y: number } {
		try {
			const raw = JSON.parse(localStorage.getItem(POSITION_KEY) ?? 'null') as { x?: unknown; y?: unknown } | null;
			if (raw && typeof raw.x === 'number' && typeof raw.y === 'number') return { x: raw.x, y: raw.y };
		} catch {
			return { x: 0, y: 0 };
		}
		return { x: 0, y: 0 };
	}

	let position = $state(readPosition());
	let drag = $state<{ x: number; y: number; base: { x: number; y: number } } | null>(null);
	let collapsed = $state(false);
	let root = $state<HTMLElement | null>(null);

	let taskId = $derived($ui.taskId);
	let task = $derived($ui.tasks.find((entry) => entry.id === taskId) ?? null);
	let entry = $derived.by(() => {
		void $editorsRevision;
		return taskId ? tasks.get('image', taskId) : undefined;
	});
	let label = $derived(entry?.label ?? task?.label ?? 'Generate');
	let needsPrompt = $derived((entry?.prompt ?? task?.prompt ?? 'required') !== 'none');
	let promptRequired = $derived((entry?.prompt ?? task?.prompt) === 'required');
	let regionRequired = $derived((entry?.region ?? task?.region) === 'required');
	let regionNone = $derived((entry?.region ?? task?.region) === 'none');
	let prompt = $derived(taskId ? ($ui.prompts[taskId] ?? '') : '');
	let results = $derived(taskId ? ($ui.results[taskId] ?? 1) : 1);
	let chip = $derived.by(() => {
		void $ui;
		return taskId ? generate.chipFor(taskId) : null;
	});
	let noModel = $derived(!!task && !task.offers.some((offer) => offer.qualifies) && !task.default);
	let chipText = $derived(noModel ? 'No model yet' : (chip ?? CHOOSE_SETUP));
	let area = $derived($maskArea);
	let blocked = $derived(
		!taskId || !chip || $ui.starting || (promptRequired && !prompt.trim()) || (regionRequired && !area)
	);
	let positionStyle = $derived(position.x || position.y ? `transform: translate(${position.x}px, ${position.y}px)` : undefined);

	function setPrompt(event: Event) {
		const field = event.currentTarget as HTMLTextAreaElement;
		if (taskId) generate.setPrompt(taskId, field.value);
		field.style.height = 'auto';
		field.style.height = `${Math.min(phone ? 112 : 96, field.scrollHeight + 2)}px`;
	}

	function fit(node: HTMLTextAreaElement) {
		node.style.height = 'auto';
		node.style.height = `${Math.min(phone ? 112 : 96, node.scrollHeight + 2)}px`;
	}

	function start() {
		if (taskId && !blocked) void generate.generate(taskId, !!area);
	}

	function stop() {
		if (running) void generate.cancel(running);
	}

	function keydown(event: KeyboardEvent) {
		if (!root || !(event.target instanceof Node) || !root.contains(event.target)) return;
		if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
			event.preventDefault();
			if (running) return;
			start();
			return;
		}
		if (event.key === 'Escape' && !$ui.picker.open) {
			event.preventDefault();
			event.stopPropagation();
			onclose?.();
		}
	}

	function pointerdown(event: PointerEvent) {
		if (phone || event.button !== 0) return;
		(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId);
		drag = { x: event.clientX, y: event.clientY, base: position };
		event.preventDefault();
	}

	function pointermove(event: PointerEvent) {
		if (drag) position = { x: drag.base.x + event.clientX - drag.x, y: drag.base.y + event.clientY - drag.y };
	}

	function pointerup() {
		if (!drag) return;
		drag = null;
		try {
			localStorage.setItem(POSITION_KEY, JSON.stringify(position));
		} catch {
			return;
		}
	}

	onDestroy(() => {
		drag = null;
	});
</script>

<svelte:window onkeydown={keydown} />

{#snippet chipButton()}
	<button
		type="button"
		class="inline-flex h-7 min-w-0 max-w-full items-center gap-2 rounded border border-line-strong bg-surface-2 px-2 text-sm text-fg transition-colors hover:border-line-hover focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-signal {chip ? '' : 'text-fg-muted'} {$ui.picker.open ? 'border-line-hover' : ''}"
		aria-haspopup="dialog"
		aria-expanded={$ui.picker.open}
		onclick={() => ($ui.picker.open ? generate.closePicker() : generate.openPicker())}
		data-setup-chip
	>
		<Icon name={noModel ? 'warning' : 'model'} className="h-3.5 w-3.5 flex-none text-fg-muted" />
		<span class="min-w-0 truncate">{chipText}</span>
		<Icon name="chevron-down" className="h-3.5 w-3.5 flex-none text-fg-muted" />
	</button>
{/snippet}

{#snippet settingsButton()}
	{#if settings}
		<IconButton icon="sliders" label="Settings" size="sm" active={settings.open} ariaPressed={settings.open} onclick={settings.toggle} data-prompt-settings />
	{:else}
		<Tooltip text="Settings open here soon" position="top">
			<IconButton icon="sliders" label="Settings" size="sm" disabled data-prompt-settings />
		</Tooltip>
	{/if}
{/snippet}

{#snippet statusLine()}
	{#if regionRequired && !area}
		<span class="flex min-w-0 items-center gap-2 text-sm text-warning" data-prompt-status="warn"><Icon name="warning" className="h-3.5 w-3.5 flex-none" />Mark the object first</span>
	{:else if !area && !regionNone}
		<span class="min-w-0 truncate whitespace-nowrap text-sm text-fg-muted" data-prompt-status="whole">Whole picture</span>
	{:else if area}
		<span class="flex min-w-0 items-center gap-2 whitespace-nowrap text-sm text-fg-muted" data-prompt-status="area">Marked area<span class="font-mono tabular-nums text-fg">{areaLabel(area)}</span></span>
	{:else}
		<span></span>
	{/if}
{/snippet}

{#snippet resultsControl()}
	<span class="inline-flex flex-none items-center gap-2 text-sm text-fg-muted">
		Results
		<span class="font-mono tabular-nums">
			<SegmentedControl variant="toggle" ariaLabel="Results" items={RESULT_ITEMS} selected={String(results)} onSelect={(id) => taskId && generate.setResults(taskId, Number(id))} />
		</span>
	</span>
{/snippet}

{#snippet actionButton()}
	{#if running}
		<Button variant="secondary" size="sm" class="h-8 flex-none" onclick={stop} attrs={{ 'data-prompt-cancel': '' }}>Cancel</Button>
	{:else}
		<Button variant="primary" size="sm" class="h-8 flex-none" disabled={blocked} loading={$ui.starting} onclick={start} attrs={{ 'data-prompt-generate': '' }}>
			Generate<Kbd keys="Ctrl ↵" />
		</Button>
	{/if}
{/snippet}

{#snippet promptField()}
	{#if needsPrompt}
		<textarea
			class="input min-h-9 resize-none"
			style="max-height: {phone ? 112 : 96}px"
			rows={phone ? 2 : 1}
			placeholder="Describe the change"
			aria-label="Describe the change"
			value={prompt}
			oninput={setPrompt}
			use:fit
			data-prompt-field
		></textarea>
	{:else}
		<div class="flex min-h-9 items-center gap-2 rounded bg-surface-2 px-3 py-2 text-base text-fg-muted" data-prompt-note>
			<Icon name={entry?.icon ?? 'sparkles'} className="h-3.5 w-3.5 flex-none" />The marked object will be removed.
		</div>
	{/if}
{/snippet}

{#snippet picker()}
	{#if $ui.picker.open && taskId}
		<SetupPicker {phone} {taskId} {label} />
	{/if}
{/snippet}

{#if phone && collapsed}
	<div bind:this={root} class="relative flex-none rounded-t-xl border-t border-line-strong bg-surface-1 px-3 pb-2 pt-1 shadow-[var(--shadow-dock)]" role="group" aria-label={label} data-floating-prompt="collapsed">
		<button type="button" class="mx-auto mb-1 block h-1 w-9 rounded-sm bg-line-strong" aria-label="Expand" onclick={() => (collapsed = false)}></button>
		<div class="flex h-9 items-center gap-2">
			<div class="min-w-0 flex-1">{@render chipButton()}</div>
			{@render actionButton()}
		</div>
		{@render picker()}
	</div>
{:else if phone}
	<div bind:this={root} class="relative flex-none rounded-t-xl border-t border-line-strong bg-surface-1 px-3 pb-3 shadow-[var(--shadow-dock)]" role="group" aria-label={label} data-floating-prompt="phone">
		<button type="button" class="mx-auto block h-4 w-full" aria-label="Collapse" onclick={() => (collapsed = true)}><span class="mx-auto block h-1 w-9 rounded-sm bg-line-strong"></span></button>
		<div class="flex h-9 items-center gap-2">
			<div class="min-w-0 flex-1">{@render chipButton()}</div>
			{@render settingsButton()}
		</div>
		<div class="mt-1">{@render promptField()}</div>
		<div class="mt-2 min-h-5">{@render statusLine()}</div>
		{#if large && !regionNone}<p class="mt-1 flex items-center gap-1.5 text-sm text-fg-subtle"><Icon name="info" className="h-3.5 w-3.5" />Large areas come back softer</p>{/if}
		{#if $ui.error}<p class="mt-1 text-sm text-danger" role="alert" data-prompt-error>{$ui.error}</p>{/if}
		<div class="mt-2 flex items-center justify-between gap-2">
			{@render resultsControl()}
			{@render actionButton()}
		</div>
		{@render picker()}
	</div>
{:else}
	<div
		bind:this={root}
		class="pointer-events-auto relative w-[480px] max-w-full rounded-xl border border-line-strong bg-surface-1 px-2.5 pb-3 shadow-floating"
		style={positionStyle}
		role="group"
		aria-label={label}
		data-floating-prompt="desktop"
	>
		<div
			class="grid h-4 cursor-grab touch-none place-items-center {drag ? 'cursor-grabbing' : ''}"
			role="presentation"
			onpointerdown={pointerdown}
			onpointermove={pointermove}
			onpointerup={pointerup}
			onpointercancel={pointerup}
			data-prompt-grip
		>
			<span class="h-[3px] w-8 rounded-sm bg-line-hover"></span>
		</div>
		<div class="flex h-9 items-center gap-2">
			<div class="min-w-0">{@render chipButton()}</div>
			<span class="flex-1"></span>
			{@render settingsButton()}
			<Kbd keys="Esc" />
		</div>
		<div class="mt-1">{@render promptField()}</div>
		<div class="mt-2.5 flex h-8 items-center gap-2">
			<div class="min-w-0 flex-1">{@render statusLine()}</div>
			{@render resultsControl()}
			{@render actionButton()}
		</div>
		{#if large && !regionNone}<p class="mt-2 flex items-center gap-1.5 text-sm text-fg-subtle" data-prompt-large><Icon name="info" className="h-3.5 w-3.5" />Large areas come back softer</p>{/if}
		{#if $ui.error}<p class="mt-2 text-sm text-danger" role="alert" data-prompt-error>{$ui.error}</p>{/if}
		{@render picker()}
	</div>
{/if}
