<script lang="ts">
	import { tick, untrack } from 'svelte';
	import type { FieldLabels } from '$lib/session/sessionDrawerModel';
	import type { SessionPanelActions, SessionPanelState } from '$lib/session/sessionDialogs';
	import type { Tab } from '$lib/types/tabs';
	import type { PresetInfo } from '$lib/services/api/index';
	import type { ReadinessReport } from '$lib/services/api/setup';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import PickerSelect from '$lib/components/preset/PickerSelect.svelte';
	import PresetPicker from '$lib/components/preset/PresetPicker.svelte';
	import PresetThumbnail from '$lib/components/preset/PresetThumbnail.svelte';
	import FormulasButton from '$lib/components/formulas/FormulasButton.svelte';
	import SessionControl from '$lib/components/session/SessionControl.svelte';
	import { Kbd, Menu, Spinner } from '$lib/components/ui';
	import { formAudienceStore } from '$lib/stores/formAudience';
	import { authStore } from '$lib/stores/auth';
	import { tabsStore } from '$lib/stores/tabs';
	import { formFoldStates, toggleFormPane } from '$lib/stores/generationLayout';
	import { shortcutLabels } from '$lib/stores/keybindings';
	import { pressFloatingForm, pressFloatingWorkbench } from '$lib/generation/floatingOverlays';
	import { presetModeOptions, type PresetModeChoice } from '$lib/utils/presetModeOptions';
	import { modeLabel } from '$lib/utils/modeLabel.svelte';
	import { resolveVariant, sortVariants } from '$lib/utils/variants';
	import { contextToolsAvailable } from '$lib/generation/contextTools';

	const TOOL_CAP = 3;
	const TOOLS_BREAKPOINT = 944;

	interface ContextTool {
		id: string;
		group: number;
		label: string;
		text: string;
		name: string;
		icon: string;
		tip: string;
		kbd?: string;
		pressed?: boolean;
		toggle: () => void;
	}

	interface Fit {
		visible: number;
		squeeze: 0 | 1 | 2;
		compact: boolean;
	}

	let {
		tab,
		presets = [],
		readiness = null,
		isLoading = false,
		isReloading = false,
		availableModes = [],
		session,
		actions,
		fieldLabels = {},
		onPresetChange,
		onModeChange,
		onVariantChange,
		onReload,
		onFormulaApplied = undefined
	}: {
		tab: Tab;
		presets?: PresetInfo[];
		readiness?: ReadinessReport | null;
		isLoading?: boolean;
		isReloading?: boolean;
		availableModes?: PresetModeChoice[];
		session: SessionPanelState;
		actions: SessionPanelActions;
		fieldLabels?: FieldLabels;
		onPresetChange: (presetId: string) => void;
		onModeChange: (mode: string) => void;
		onVariantChange: (variant: string) => void;
		onReload: () => void;
		onFormulaApplied?: () => void;
	} = $props();

	const uid = $props.id();

	let barEl = $state<HTMLElement>();
	let contextEl = $state<HTMLElement>();
	let pinnedEl = $state<HTMLElement>();
	let fit = $state<Fit>({ visible: TOOL_CAP, squeeze: 0, compact: false });
	let fitToken = 0;

	let presetInfo = $derived(presets.find((preset) => preset.id === tab.selectedPreset));
	let modeOptions = $derived(presetModeOptions(availableModes, { admin: $authStore.user?.account_type === 'ADMIN' }));
	let currentModeLabel = $derived(modeLabel(tab.selectedMode, availableModes.find((mode) => mode.id === tab.selectedMode)?.label));
	let variants = $derived(sortVariants(availableModes.find((mode) => mode.id === tab.selectedMode)?.variants));
	let variantOptions = $derived(variants.map((variant) => ({ value: variant.name, label: variant.label, description: variant.description })));
	let selectedVariant = $derived(resolveVariant(variants, tab.selectedVariant));
	let hasPreset = $derived(!!tab.selectedPreset);
	let showMode = $derived(hasPreset && modeOptions.length > 1);
	let showVariant = $derived(hasPreset && variantOptions.length > 1);
	let showSession = $derived(hasPreset && !!tab.selectedMode);
	let showTools = $derived(contextToolsAvailable(tab));
	let disclosureKey = $derived([showTools, showMode, showVariant, showSession].join('|'));

	let advanced = $derived($formAudienceStore === 'advanced');
	let threePanes = $derived(tab.layoutMode === 'three');
	let foldState = $derived($formFoldStates[tab.id]);
	let folded = $derived(!!tab.leftPanelCollapsed || (!!foldState?.canAutoFold && !foldState.unfolded));

	let tools = $derived<ContextTool[]>([
		{
			id: 'fields',
			group: 1,
			label: 'Field visibility',
			text: advanced ? 'Advanced' : 'Simple',
			name: `Field visibility: ${advanced ? 'Advanced' : 'Simple'}`,
			icon: 'list',
			tip: 'Simple hides advanced-only fields; their values still submit with saved/default settings.',
			pressed: advanced,
			toggle: () => formAudienceStore.toggle()
		},
		{
			id: 'layout',
			group: 1,
			label: 'Layout',
			text: threePanes ? 'Three panes' : 'Two panes',
			name: `Layout: ${threePanes ? 'Three panes' : 'Two panes'}`,
			icon: 'layout-template',
			tip: threePanes ? 'Switch to two panes' : 'Switch to three panes',
			toggle: () => tabsStore.updateTab(tab.id, { layoutMode: threePanes ? 'two' : 'three' })
		},
		{
			id: 'float-form',
			group: 2,
			label: 'Float form',
			text: 'Float form',
			name: 'Float form',
			icon: 'layers',
			tip: tab.formFloating ? 'Dock the form' : 'Float the form',
			kbd: $shortcutLabels['toggle_floating_form'],
			pressed: !!tab.formFloating,
			toggle: () => tabsStore.updateTab(tab.id, pressFloatingForm(tab))
		},
		{
			id: 'float-workbench',
			group: 2,
			label: 'Float workbench',
			text: 'Float workbench',
			name: 'Float workbench',
			icon: 'photo',
			tip: tab.workbenchFloating ? 'Dock the workbench' : 'Float the workbench',
			kbd: $shortcutLabels['toggle_floating_workbench'],
			pressed: !!tab.workbenchFloating,
			toggle: () => tabsStore.updateTab(tab.id, pressFloatingWorkbench(tab))
		},
		{
			id: 'fold-form',
			group: 2,
			label: 'Fold form',
			text: 'Fold form',
			name: 'Fold form',
			icon: 'chevron-left',
			tip: folded ? 'Unfold the form' : 'Fold the form',
			kbd: $shortcutLabels['toggle_left_panel'],
			pressed: folded,
			toggle: () => {
				const patch = toggleFormPane(tab.id, !!tab.leftPanelCollapsed);
				if (patch) tabsStore.updateTab(tab.id, patch);
			}
		}
	]);
	let shownTools = $derived(tools.slice(0, fit.visible));
	let foldedTools = $derived(tools.slice(fit.visible));
	let foldedActive = $derived(foldedTools.some((tool) => tool.pressed));
	let labelClass = $derived(fit.compact ? 'sr-only' : 'ctx-label whitespace-nowrap text-fg-subtle');
	let stepPad = $derived(fit.compact ? 'px-2.5' : 'px-4');
	let capClass = $derived(fit.squeeze === 2 ? 'max-w-[96px]' : fit.squeeze === 1 ? 'max-w-[160px]' : '');

	let fitKey = $derived(
		[
			tab.selectedPreset,
			tab.selectedMode,
			selectedVariant,
			session.currentSession?.name,
			session.sessionControlsEnabled,
			disclosureKey,
			presetInfo?.name,
			...tools.map((tool) => tool.text)
		].join('|')
	);

	function controlClass(open: boolean): string {
		return `ctx-select flex min-w-0 items-center text-left text-fg disabled:cursor-not-allowed disabled:opacity-50 ${open ? 'is-open' : ''}`;
	}

	function toolClass(pressed: boolean | undefined): string {
		return `flex h-8 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded border px-2.5 text-sm transition-colors ${
			pressed ? 'border-signal/50 bg-signal/10 text-signal' : 'border-line bg-control-fill text-fg hover:border-line-hover hover:bg-control-fill-hover'
		}`;
	}

	function overflowing(): boolean {
		if (!barEl) return false;
		if (barEl.scrollWidth > barEl.clientWidth + 1) return true;
		return !!contextEl && contextEl.scrollWidth > contextEl.clientWidth + 1;
	}

	async function refit() {
		const token = ++fitToken;
		const el = barEl;
		if (!el) return;
		const pinned = pinnedEl?.querySelectorAll('[data-testid="formulas-button"]').length ?? 0;
		const narrow = el.clientWidth > 0 && el.clientWidth < TOOLS_BREAKPOINT;
		let next: Fit = { visible: narrow ? 0 : Math.max(0, Math.min(tools.length, TOOL_CAP - pinned)), squeeze: 0, compact: false };
		fit = next;
		await tick();
		while (token === fitToken && next.visible > 0 && overflowing()) {
			next = { ...next, visible: next.visible - 1 };
			fit = next;
			await tick();
		}
		for (const squeeze of [1, 2] as const) {
			if (token !== fitToken || !overflowing()) return;
			next = { ...next, squeeze };
			fit = next;
			await tick();
		}
		if (token !== fitToken || !overflowing()) return;
		fit = { ...next, compact: true };
	}

	$effect(() => {
		void fitKey;
		untrack(() => void refit());
	});

	$effect(() => {
		void disclosureKey;
		if (!barEl || typeof ResizeObserver === 'undefined') return;
		const observer = new ResizeObserver(() => void refit());
		observer.observe(barEl);
		if (pinnedEl) observer.observe(pinnedEl);
		const growth = new ResizeObserver(() => {
			if (overflowing()) void refit();
		});
		if (contextEl) for (const step of contextEl.querySelectorAll(':scope > [data-context-step]')) growth.observe(step);
		return () => {
			observer.disconnect();
			growth.disconnect();
		};
	});

	function chooseVariant(value: string) {
		if (!value || value === selectedVariant) return;
		onVariantChange(value);
	}
</script>

{#snippet cut()}
	<span class="context-cut" aria-hidden="true" data-testid="context-separator">
		<svg viewBox="0 0 20 48" preserveAspectRatio="none" focusable="false"><path d="M-4.25 -6 L17 24 L-4.25 54"></path></svg>
	</span>
{/snippet}

{#snippet divider()}
	<span class="mx-1 h-5 w-px flex-shrink-0 bg-line-strong" aria-hidden="true"></span>
{/snippet}

<div
	bind:this={barEl}
	class="flex h-12 flex-shrink-0 items-stretch overflow-hidden border-b border-line bg-canvas"
	data-testid="context-bar"
>
	<div
		bind:this={contextEl}
		class="flex min-w-0 flex-shrink items-center"
		role="group"
		aria-label="Generation context"
		data-testid="context-zone"
	>
		<div class="ctx-step flex flex-shrink-0 {stepPad}" data-context-step>
			<span id="{uid}-preset" class={labelClass} data-context-label>Preset</span>
			<div class="flex min-w-0" data-testid="preset-header-picker">
				<PresetPicker
					{presets}
					selectedPreset={tab.selectedPreset || ''}
					{readiness}
					loading={isLoading}
					{isReloading}
					on:reload={onReload}
					on:select={(event) => onPresetChange(event.detail)}
				>
					{#snippet trigger(picker)}
						<Tooltip text={picker.currentPreset?.name ?? ''} truncateOnly position="bottom" delay={400} wrapperClass="flex min-w-0">
							<button
								type="button"
								class="{controlClass(picker.expanded)} {capClass || 'min-w-[132px] max-w-[240px]'} text-md font-semibold"
								class:preset-picker-cta={!picker.currentPreset && !picker.loading}
								aria-haspopup="dialog"
								aria-expanded={picker.expanded}
								aria-labelledby="{uid}-preset {uid}-preset-value"
								disabled={picker.disabled}
								data-testid="preset-picker-trigger"
								onclick={picker.openPicker}
							>
								<span class="ctx-valuerow">
									{#if picker.loading}
										<Spinner size="sm" />
									{:else if picker.currentPreset}
										<PresetThumbnail
											presetId={picker.currentPreset.id}
											presetName={picker.currentPreset.name}
											cover={picker.currentPreset.media?.cover}
											category={picker.currentPreset.category}
											size="w-[18px] h-[18px]"
											zoom={false}
										/>
									{/if}
									<span id="{uid}-preset-value" class="min-w-0 truncate">{picker.currentPreset?.name || 'Choose a preset'}</span>
								</span>
								<Icon name="chevron-down" className="h-[13px] w-[13px] flex-shrink-0 text-fg-subtle transition-transform {picker.expanded ? 'rotate-180' : ''}" />
							</button>
						</Tooltip>
					{/snippet}
				</PresetPicker>
			</div>
		</div>

		{#if showMode}
			{@render cut()}
			<div class="ctx-step flex flex-shrink-0 {stepPad}" data-testid="preset-header-mode" data-context-step>
				<span id="{uid}-mode" class={labelClass} data-context-label>Mode</span>
				<PickerSelect
					value={tab.selectedMode ?? ''}
					options={modeOptions}
					label="Choose mode"
					width={400}
					onchange={onModeChange}
				>
					{#snippet trigger(select)}
						<Tooltip text={select.selected?.label ?? ''} truncateOnly position="bottom" delay={400} wrapperClass="flex min-w-0">
							<button
								type="button"
								class="{controlClass(select.open)} {capClass || 'min-w-[132px] max-w-[240px]'} text-base font-medium"
								aria-haspopup="listbox"
								aria-expanded={select.open}
								aria-labelledby="{uid}-mode {uid}-mode-value"
																onclick={select.toggle}
								onkeydown={select.keydown}
							>
								<span class="ctx-valuerow">
									{#if select.selected?.icon}
										<Icon name={select.selected.icon} className="h-3.5 w-3.5 flex-shrink-0 text-fg-muted" />
									{/if}
									<span id="{uid}-mode-value" class="min-w-0 truncate">{select.selected?.label ?? 'Choose a mode'}</span>
									{#if select.selected?.marker}
										<span class="-ml-1 flex-shrink-0 font-mono text-sm text-fg-subtle" aria-hidden="true">{select.selected.marker}</span>
									{/if}
								</span>
								<Icon name="chevron-down" className="h-[13px] w-[13px] flex-shrink-0 text-fg-subtle transition-transform {select.open ? 'rotate-180' : ''}" />
							</button>
						</Tooltip>
					{/snippet}
				</PickerSelect>
			</div>
		{/if}

		{#if showVariant}
			{@render cut()}
			<div class="ctx-step flex flex-shrink-0 {stepPad}" data-testid="preset-variant-switch" data-context-step>
				<span id="{uid}-variant" class={labelClass} data-context-label>Variant</span>
				<PickerSelect
					value={selectedVariant}
					options={variantOptions}
					label="Choose variant"
					width={360}
					onchange={chooseVariant}
				>
					{#snippet trigger(select)}
						<Tooltip text={select.selected?.label ?? ''} truncateOnly position="bottom" delay={400} wrapperClass="flex min-w-0">
							<button
								type="button"
								class="{controlClass(select.open)} {capClass || 'min-w-[132px] max-w-[200px]'} text-base font-medium"
								aria-haspopup="listbox"
								aria-expanded={select.open}
								aria-labelledby="{uid}-variant {uid}-variant-value"
								onclick={select.toggle}
								onkeydown={select.keydown}
							>
								<span class="ctx-valuerow">
									<span id="{uid}-variant-value" class="min-w-0 truncate">{select.selected?.label ?? ''}</span>
								</span>
								<Icon name="chevron-down" className="h-[13px] w-[13px] flex-shrink-0 text-fg-subtle transition-transform {select.open ? 'rotate-180' : ''}" />
							</button>
						</Tooltip>
					{/snippet}
				</PickerSelect>
			</div>
		{/if}

		{#if showSession}
			{@render cut()}
			<div class="ctx-step flex min-w-0 shrink {stepPad}" data-context-step>
				<span id="{uid}-session" class={labelClass} data-context-label>Session</span>
				<SessionControl {session} {actions} {fieldLabels} compact={fit.compact} labelId="{uid}-session" />
			</div>
		{/if}
	</div>

	<div class="min-w-0 flex-1" data-testid="context-gap"></div>

	{#if showTools}
		<div class="flex flex-shrink-0 items-center gap-1 px-2.5" role="group" aria-label="Generation tools" data-testid="context-tools">
			<div bind:this={pinnedEl} class="pinned flex items-center">
				<FormulasButton
					{tab}
					layout="rail"
					presetName={presetInfo?.name ?? ''}
					presetVersion={presetInfo?.version ?? ''}
					modeLabel={currentModeLabel}
					onApplied={onFormulaApplied}
				/>
				{@render divider()}
			</div>

			{#each shownTools as tool, index (tool.id)}
				{#if index > 0 && tool.group !== shownTools[index - 1].group}
					{@render divider()}
				{/if}
				<Tooltip text={tool.tip} kbd={tool.kbd} position="bottom" delay={300}>
					<button
						type="button"
						class={toolClass(tool.pressed)}
						aria-label={tool.name}
						aria-pressed={tool.pressed === undefined ? undefined : tool.pressed}
						data-context-tool={tool.id}
						onclick={tool.toggle}
					>
						<Icon name={tool.icon} className="h-4 w-4 flex-shrink-0" />
						<span>{tool.text}</span>
					</button>
				</Tooltip>
			{/each}

			{#if foldedTools.length}
				{#if shownTools.length}
					{@render divider()}
				{/if}
				<Menu label="More tools" width={260} align="right">
					{#snippet trigger(menu)}
						<button
							type="button"
							class="relative flex h-8 flex-shrink-0 items-center gap-1.5 rounded border px-2.5 transition-colors {menu.open
								? 'border-line-hover bg-control-fill-hover text-fg'
								: 'border-line bg-control-fill text-fg hover:border-line-hover hover:bg-control-fill-hover'}"
							aria-label="More tools"
							aria-haspopup="menu"
							aria-expanded={menu.open}
							data-testid="context-tools-more"
							onclick={menu.toggle}
						>
							<Icon name="more" className="h-4 w-4" />
							<span class="font-mono text-sm tabular-nums text-fg-subtle">{foldedTools.length}</span>
							{#if foldedActive}
								<span class="absolute right-[3px] top-[3px] h-1.5 w-1.5 rounded-full bg-signal" data-testid="context-tools-more-active"></span>
							{/if}
						</button>
					{/snippet}
					{#snippet children(menu)}
						{#each foldedTools as tool (tool.id)}
							<button
								type="button"
								role={tool.pressed === undefined ? 'menuitem' : 'menuitemcheckbox'}
								aria-checked={tool.pressed === undefined ? undefined : tool.pressed}
								class="flex h-9 w-full items-center gap-2.5 rounded px-2.5 text-left text-sm hover:bg-surface-3 focus-visible:bg-surface-3 focus-visible:outline-none {tool.pressed
									? 'text-signal'
									: 'text-fg'}"
								aria-label={tool.name}
								onclick={() => {
									menu.close(true);
									tool.toggle();
								}}
							>
								<Icon name={tool.icon} className="h-4 w-4 flex-shrink-0" />
								<span class="min-w-0 flex-1 truncate">{tool.label}</span>
								{#if tool.kbd}
									<Kbd keys={tool.kbd} />
								{:else if tool.kbd == null && tool.label !== tool.text}
									<span class="flex-shrink-0 font-mono text-sm text-fg-subtle">{tool.text}</span>
								{/if}
							</button>
						{/each}
					{/snippet}
				</Menu>
			{/if}
		</div>
	{/if}
</div>

<style>
	.context-cut {
		position: relative;
		display: block;
		width: 20px;
		flex-shrink: 0;
		align-self: stretch;
	}

	.context-cut svg {
		position: absolute;
		inset: 0;
		display: block;
		width: 100%;
		height: 100%;
		overflow: visible;
	}

	.context-cut path {
		fill: none;
		stroke: rgb(var(--surface-1));
		stroke-width: 3;
		vector-effect: non-scaling-stroke;
	}

	.pinned:not(:has(:global([data-testid='formulas-button']))) {
		display: none;
	}
</style>
