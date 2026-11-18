<script lang="ts">
	import type { Snippet } from 'svelte';
	import GenerateMark from '$lib/components/generation-panel/GenerateMark.svelte';
	import BorderProgress from '$lib/components/generation-panel/BorderProgress.svelte';
	import { borderProgressOf, type BorderRun } from '$lib/components/generation-panel/borderProgress';
	import { blockedReason, markOf } from '$lib/tool-tabs/image/generate/mark';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, Kbd } from '$lib/components/ui';
	import { tasks } from '$lib/editors/tasks';
	import { editorsRevision } from '$lib/editors/registry';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { areaLabel } from '$lib/tool-tabs/image/generate/area';
	import { maskArea } from '$lib/tool-tabs/image/maskOverlays';
	import EditorFormulas from './EditorFormulas.svelte';
	import EditorPickers from './EditorPickers.svelte';
	import GenerateDockLayout from './GenerateDockLayout.svelte';
	import EditorNotices from './EditorNotices.svelte';

	let {
		phone = false,
		running = null,
		large = false,
		settings,
		tempRow,
		hasTemp = false,
		run = null,
		extras
	}: {
		phone?: boolean;
		running?: string | null;
		large?: boolean;
		settings?: { open: boolean; toggle: () => void };
		tempRow?: Snippet;
		hasTemp?: boolean;
		run?: BorderRun | null;
		extras?: Snippet;
	} = $props();

	const generate = getGenerateController()!;
	const ui = generate.ui;

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
	let chip = $derived.by(() => {
		void $ui;
		return taskId ? generate.chipFor(taskId) : null;
	});
	let noModel = $derived(!!task && !task.offers.some((offer) => offer.qualifies) && !task.default);
	let changed = $derived.by(() => {
		void $ui;
		return taskId ? generate.changedFor(taskId) : false;
	});
	let chipText = $derived(noModel ? 'No model yet' : (chip ?? 'Choose a preset'));
	let area = $derived($maskArea);
	let redoArea = $derived(!!$ui.redo && $ui.redo.taskId === taskId && !regionNone && !area);
	let focused = $state(false);
	let reason = $derived(
		blockedReason({ hasSetup: !!chip, promptRequired, prompt, regionRequired, hasArea: !!area, redoArea })
	);
	let borderProgress = $derived(borderProgressOf(run));
	let runText = $derived(borderProgress.state === 'running' ? `Generating ${borderProgress.percent}%` : borderProgress.state === 'loading' ? 'Waiting' : null);
	let markInfo = $derived(markOf({ running: !!running, starting: $ui.starting, reason }));
	let noteShown = $derived(!!$ui.picker.notice || (task?.offers.length ?? 0) === 0);
	let blocked = $derived(
		!taskId || !chip || $ui.starting || (promptRequired && !prompt.trim()) || (regionRequired && !area) || redoArea
	);

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

	function markClick() {
		if (markInfo.state === 'running') stop();
		else if (!markInfo.disabled) start();
	}

	function stop() {
		if (running) void generate.cancel(running);
	}

	function keydown(event: KeyboardEvent) {
		if (!root || !(event.target instanceof Node) || !root.contains(event.target)) return;
		if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
			event.preventDefault();
			if (!running) start();
		}
	}
</script>

<svelte:window onkeydown={keydown} />

{#snippet summaryButton()}
	<button
		type="button"
		class="inline-flex h-7 min-w-0 max-w-full items-center gap-2 rounded border border-line-strong bg-surface-2 px-2 text-sm text-fg transition-colors hover:border-line-hover focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-signal {chip ? '' : 'text-fg-muted'}"
		aria-label="Show the setup"
		onclick={() => (collapsed = false)}
		data-prompt-summary
	>
		<Icon name={noModel ? 'warning' : 'model'} className="h-3.5 w-3.5 flex-none text-fg-muted" />
		<span class="min-w-0 truncate">{chipText}</span>
		{#if changed}
			<span class="inline-flex flex-none items-center gap-1.5 font-mono text-sm text-signal" data-setup-changed><span class="h-1.5 w-1.5 rounded-full bg-signal"></span>Changed</span>
		{/if}
	</button>
{/snippet}

{#snippet changedMark()}
	{#if changed}
		<span class="dock-ctl inline-flex flex-none items-center gap-1.5 font-mono text-sm text-signal" data-setup-changed><span class="h-1.5 w-1.5 rounded-full bg-signal"></span>Changed</span>
	{/if}
{/snippet}

{#snippet settingsButton()}
	<span class="dock-btn">
		{#if settings}
			<Button size="sm" icon="sliders" class="h-8 text-base" pressed={settings.open} ariaLabel="Settings" onclick={settings.toggle} attrs={{ 'data-prompt-settings': '' }}>Settings</Button>
		{:else}
			<Tooltip text="Settings open here soon" position="top">
				<Button size="sm" icon="sliders" class="h-8 text-base" disabled ariaLabel="Settings" attrs={{ 'data-prompt-settings': '' }}>Settings</Button>
			</Tooltip>
		{/if}
	</span>
{/snippet}

{#snippet statusLine()}
	{#if redoArea}
		<span class="flex min-w-0 items-center gap-2 text-sm text-warning" data-prompt-status="redo"><Icon name="warning" className="h-3.5 w-3.5 flex-none" />Mark the area again</span>
	{:else if regionRequired && !area}
		<span class="flex min-w-0 items-center gap-2 text-sm text-warning" data-prompt-status="warn"><Icon name="warning" className="h-3.5 w-3.5 flex-none" />Mark the object first</span>
	{:else if !area && !regionNone}
		<span class="min-w-0 truncate whitespace-nowrap text-sm text-fg-muted" data-prompt-status="whole">Whole picture</span>
	{:else if area}
		<span class="flex min-w-0 items-center gap-2 whitespace-nowrap text-sm text-fg-muted" data-prompt-status="area">Marked area<span class="font-mono tabular-nums text-fg">{areaLabel(area)}</span></span>
	{:else}
		<span></span>
	{/if}
{/snippet}

{#snippet actionButton()}
	{@render dockMark(true)}
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

{#snippet dockTemp()}
	{@render tempRow?.()}
	{#if redoArea}
		<p class="m-0 flex min-h-8 items-center gap-2 text-sm text-warning" role="alert"><Icon name="warning" className="h-3.5 w-3.5 flex-none" /><span><b class="font-semibold" data-prompt-status="redo">Mark the area again</b> <span class="text-fg-subtle" data-prompt-redo>The picture beneath changed shape</span></span></p>
	{/if}
	{#if large && !regionNone}<p class="m-0 flex min-h-8 items-center gap-2 text-sm text-fg-subtle" data-prompt-large><Icon name="info" className="h-3.5 w-3.5 flex-none" />Large areas come back softer</p>{/if}
	{#if $ui.error}<p class="m-0 flex min-h-8 items-center gap-2 text-sm text-danger" role="alert" data-prompt-error>{$ui.error}</p>{/if}
{/snippet}

{#snippet dockField()}
	{#if needsPrompt}
		<div class="ai-ring min-w-0 flex-1 rounded {focused ? 'ai-ring--on' : ''}">
			<textarea
				class="block min-h-10 w-full resize-none border-0 bg-transparent px-3 py-2 text-base text-fg outline-none placeholder:text-fg-subtle focus:ring-0"
				style="max-height: 96px"
				rows="1"
				placeholder="Describe the change"
				aria-label="Describe the change"
				value={prompt}
				oninput={setPrompt}
				onfocus={() => (focused = true)}
				onblur={() => (focused = false)}
				use:fit
				data-prompt-field
			></textarea>
		</div>
	{:else}
		<div class="flex min-h-10 min-w-0 flex-1 items-center gap-2.5 rounded bg-surface-2 px-3 text-base text-fg-muted" data-prompt-note>
			<Icon name={entry?.icon ?? 'sparkles'} className="h-3.5 w-3.5 flex-none" />The marked object will be removed.
		</div>
	{/if}
{/snippet}

{#snippet dockMark(compact: boolean)}
	<span class="generate-mark-host flex-none" style={compact ? '--mark-size: 60px; --mark-glyph: 54px' : undefined} data-prompt-mark={markInfo.state}>
		<Tooltip text={markInfo.state === 'running' && runText ? `${markInfo.label}. ${runText}` : markInfo.label} kbd={markInfo.state !== 'running' ? 'Ctrl ↵' : undefined} position="top" delay={150}>
			<GenerateMark state={markInfo.state} disabled={markInfo.disabled} label={markInfo.label} onclick={markClick} />
		</Tooltip>
	</span>
{/snippet}

{#snippet pickersView()}
	{#if taskId}
		<EditorPickers {phone} {taskId} {label} {extras} changedMark={phone ? undefined : changedMark} settingsButton={phone ? undefined : settingsButton} />
	{/if}
{/snippet}

{#if phone && collapsed}
	<div bind:this={root} class="relative flex-none rounded-t-xl border-t border-line-strong bg-surface-1 px-3 pb-2 pt-1 shadow-[var(--shadow-dock)]" role="group" aria-label={label} data-floating-prompt="collapsed">
		<button type="button" class="mx-auto mb-1 block h-1 w-9 rounded-sm bg-line-strong" aria-label="Expand" onclick={() => (collapsed = false)}></button>
		<div class="flex h-9 items-center gap-2">
			<div class="min-w-0 flex-1">{@render summaryButton()}</div>
			{@render actionButton()}
		</div>
	</div>
{:else if phone}
	<div bind:this={root} class="relative flex-none rounded-t-xl border-t border-line-strong bg-surface-1 px-3 pb-3 shadow-[var(--shadow-dock)]" role="group" aria-label={label} data-floating-prompt="phone">
		<button type="button" class="mx-auto block h-4 w-full" aria-label="Collapse" onclick={() => (collapsed = true)}><span class="mx-auto block h-1 w-9 rounded-sm bg-line-strong"></span></button>
		<div class="flex h-10 items-center justify-end gap-2 [&_.dock-btn_button]:min-h-10">
			{#if taskId}<EditorFormulas {taskId} phone />{/if}
			{@render changedMark()}
			{@render settingsButton()}
		</div>
		<div class="mt-0.5">{@render pickersView()}</div>
		<div class="mt-2">{@render promptField()}</div>
		<div class="mt-2 min-h-5">{@render statusLine()}</div>
		{#if redoArea}<p class="mt-1 flex items-center gap-1.5 text-sm text-fg-subtle" data-prompt-redo><Icon name="info" className="h-3.5 w-3.5" />The picture beneath changed shape</p>{/if}
		{#if large && !regionNone}<p class="mt-1 flex items-center gap-1.5 text-sm text-fg-subtle"><Icon name="info" className="h-3.5 w-3.5" />Large areas come back softer</p>{/if}
		{#if $ui.error}<p class="mt-1 text-sm text-danger" role="alert" data-prompt-error>{$ui.error}</p>{/if}
		<div class="mt-2 flex items-center justify-end gap-2">
			{@render actionButton()}
		</div>
	</div>
{:else}
	<div bind:this={root} class="contents">
		<GenerateDockLayout {label} showTemp={hasTemp || noteShown || !!$ui.error || redoArea || (large && !regionNone)}>
			{#snippet progress()}
				<BorderProgress progress={borderProgress} />
			{/snippet}
			{#snippet strip()}
				{@render pickersView()}
			{/snippet}
			{#snippet temp()}
				{#if taskId}<EditorNotices {taskId} hint={false} />{/if}
				{@render dockTemp?.()}
			{/snippet}
			{#snippet prompt()}
				{@render dockField()}
			{/snippet}
			{#snippet mark()}
				{@render dockMark(false)}
			{/snippet}
		</GenerateDockLayout>
	</div>
{/if}
