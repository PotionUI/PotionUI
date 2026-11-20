<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import PickerSelect from '$lib/components/preset/PickerSelect.svelte';
	import PresetPicker, { PRESET_PLACEHOLDER } from '$lib/components/preset/PresetPicker.svelte';
	import PresetThumbnail from '$lib/components/preset/PresetThumbnail.svelte';
	import ContextCut from '$lib/components/ui/ContextCut.svelte';
	import EditorNotices from './EditorNotices.svelte';
	import EditorFormulas from './EditorFormulas.svelte';
	import EditorDefaultButton from './EditorDefaultButton.svelte';
	import SessionControl from '$lib/components/session/SessionControl.svelte';
	import SessionHost from '$lib/components/session/SessionHost.svelte';
	import { Spinner } from '$lib/components/ui';
	import { confirmDialog } from '$lib/stores/confirm';
	import { createSessionDialogs } from '$lib/session/sessionDialogs';
	import { loadFieldLabels } from '$lib/session/sessionFieldLabels';
	import type { FieldLabels } from '$lib/session/sessionDrawerModel';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { createEditorSessionController } from '$lib/tool-tabs/image/generate/editorSessions';
	import { NO_FORMULA, formulaOptionsOf, modeOptionsOf, presetDisabledReasons, presetListOf } from '$lib/tool-tabs/image/generate/pickers';
	import { offerOf, sameChoice, setupName, sourceOf, type SetupChoice } from '$lib/tool-tabs/image/generate/setup';
	import { isChanged } from '$lib/tool-tabs/image/generate/tweaks';
	import { DOCK_CELL, DOCK_END, DOCK_PICKERS, DOCK_STEP } from '$lib/tool-tabs/image/generate/dockStrip';

	let { phone = false, taskId, label, extras, changedMark, settingsButton }: { phone?: boolean; taskId: string; label: string; extras?: Snippet; changedMark?: Snippet; settingsButton?: Snippet } = $props();

	const generate = getGenerateController()!;
	const ui = generate.ui;
	const uid = $props.id();

	const sessionController = createEditorSessionController(generate, () => taskId);
	const dialogs = createSessionDialogs(sessionController);
	const session = sessionController.state;
	const sessionActions = { ...dialogs.actions, openScript: undefined };

	let presetPicker = $state<{ openAt(element: HTMLElement | undefined): void } | undefined>();
	let presetButton = $state<HTMLElement>();
	let fieldLabels = $state<FieldLabels>({});

	let task = $derived($ui.tasks.find((entry) => entry.id === taskId) ?? null);
	let choice = $derived($ui.choice[taskId] ?? null);
	let offer = $derived(task && choice ? offerOf(task.offers, choice) : null);
	let offers = $derived(task?.offers ?? []);
	let presets = $derived(presetListOf(offers, $ui.catalog));
	let reasons = $derived(presetDisabledReasons(offers));
	let modeOptions = $derived(modeOptionsOf(offers, choice?.preset_id));
	let showMode = $derived(!!choice && modeOptions.length > 1);
	let choices = $derived.by(() => {
		void $ui;
		return offer ? generate.choicesOf(offer) : { sessions: [], formulas: [] };
	});
	let formulaOptions = $derived(formulaOptionsOf(choices.formulas));
	let showFormulas = $derived(!!choice && choices.formulas.length > 0);
	let formulaValue = $derived(choice?.setup.kind === 'formula' ? choice.setup.id : NO_FORMULA);
	let changed = $derived(isChanged($ui.tweaks[taskId]));
	let nothingOffered = $derived(offers.length === 0);
	let sessionWidth = $derived($session.currentSession ? 'min-w-[250px] max-w-[538px]' : 'min-w-[230px] max-w-[500px]');

	$effect(() => {
		const wanted = $ui.picker.open && $ui.picker.why === 'first_use';
		if (!wanted || !presetPicker || (!phone && !presetButton)) return;
		presetPicker.openAt(presetButton);
		generate.pickerHandled();
	});

	$effect(() => {
		const preset = offer?.preset_id ?? null;
		const mode = offer?.mode ?? null;
		let live = true;
		void loadFieldLabels(preset, mode).then((labels) => {
			if (live) fieldLabels = labels;
		});
		return () => {
			live = false;
		};
	});

	async function request(next: SetupChoice | null) {
		if (!next || !choice) {
			if (next) await generate.pick(taskId, next);
			return;
		}
		if (changed && !sameChoice(next, choice)) {
			const name = offer ? setupName(sourceOf(choice.setup, choices)) : 'this setup';
			const discard = await confirmDialog({
				title: 'Discard your changes?',
				message: `Your changes to “${name}” are used only for this edit unless you save them.`,
				variant: 'danger',
				confirmLabel: 'Discard',
				cancelLabel: 'Keep editing'
			});
			if (!discard) return;
		}
		await generate.pick(taskId, next);
	}

	function pickPreset(presetId: string) {
		if (choice?.preset_id === presetId) return;
		void request(generate.choiceForPreset(taskId, presetId));
	}

	function pickMode(mode: string) {
		if (choice?.mode === mode) return;
		void request(generate.choiceForMode(taskId, mode));
	}

	function pickFormula(id: string) {
		if (!choice || id === formulaValue) return;
		void request({ preset_id: choice.preset_id, mode: choice.mode, setup: id === NO_FORMULA ? { kind: 'defaults' } : { kind: 'formula', id } });
	}

	function controlClass(open: boolean): string {
		return `ctx-select flex min-w-0 items-center text-left text-fg disabled:cursor-not-allowed disabled:opacity-50 ${open ? 'is-open' : ''}`;
	}
</script>

{#snippet stepLabel(id: string, text: string)}
	<span {id} class={phone ? 'sr-only' : 'ctx-label whitespace-nowrap text-fg-subtle'}>{text}</span>
{/snippet}

<div class="min-w-0" data-editor-pickers={phone ? 'phone' : 'desktop'}>
	{#if phone}
		<EditorNotices {taskId} />
		<div class="flex flex-col gap-2" data-testid="preset-header-row">
			<div class="flex flex-wrap items-stretch gap-1.5">
				<div class="min-w-0 flex-1" data-testid="preset-header-picker">
					<PresetPicker bind:this={presetPicker} {presets} selectedPreset={choice?.preset_id ?? ''} disabled={nothingOffered} disabledReasons={reasons} recordRecent={false} on:select={(event) => pickPreset(event.detail)} />
				</div>
				{#if showMode}
					<div class="flex w-[168px] flex-shrink-0" data-testid="preset-header-mode">
						<PickerSelect value={choice?.mode ?? ''} options={modeOptions} label="Choose mode" placeholder="Mode" width={400} onchange={pickMode} />
					</div>
				{/if}
			</div>
			<div class="flex items-center gap-1.5">
				<div class="min-w-0 flex-1">
					{#if choice}
						<SessionControl session={$session} actions={sessionActions} {fieldLabels} layout="sheet" autosave={false} shortcut={false} />
					{/if}
				</div>
				<EditorDefaultButton {taskId} {label} phone />
			</div>
			{#if showFormulas}
				<PickerSelect value={formulaValue} options={formulaOptions} label="Choose formula" placeholder="Formula" onchange={pickFormula} />
			{/if}
		</div>
	{:else}
		<div class="dock-zone flex h-12 min-w-0 items-stretch" role="group" aria-label="Setup for {label}" data-testid="context-zone" style="--cut-stroke: var(--canvas)">
			<div class={DOCK_PICKERS} data-dock-pickers>
				<div class={DOCK_STEP.preset} data-context-step>
					{@render stepLabel(`${uid}-preset`, 'Preset')}
					<div class="flex min-w-0" data-testid="preset-header-picker">
						<PresetPicker bind:this={presetPicker} {presets} selectedPreset={choice?.preset_id ?? ''} disabled={nothingOffered} disabledReasons={reasons} recordRecent={false} on:select={(event) => pickPreset(event.detail)}>
							{#snippet trigger(picker)}
								<Tooltip text={picker.currentPreset?.name ?? ''} truncateOnly position="bottom" delay={400} wrapperClass="flex min-w-0">
									<button
										bind:this={presetButton}
										type="button"
										class="{controlClass(picker.expanded)} {DOCK_CELL.preset} text-base {picker.currentPreset ? 'font-semibold' : 'font-medium'}"
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
												<PresetThumbnail presetId={picker.currentPreset.id} presetName={picker.currentPreset.name} cover={picker.currentPreset.media?.cover} category={picker.currentPreset.category} size="w-[18px] h-[18px]" zoom={false} />
											{/if}
											<span id="{uid}-preset-value" class="min-w-0 truncate {picker.currentPreset ? '' : 'text-fg-muted'}" data-preset-value>{picker.currentPreset?.name || PRESET_PLACEHOLDER}</span>
										</span>
										<Icon name="chevron-down" className="h-[13px] w-[13px] flex-shrink-0 text-fg-subtle transition-transform {picker.expanded ? 'rotate-180' : ''}" />
									</button>
								</Tooltip>
							{/snippet}
						</PresetPicker>
					</div>
				</div>

				{#if showMode}
					<ContextCut />
					<div class={DOCK_STEP.mode} data-testid="preset-header-mode" data-context-step>
						{@render stepLabel(`${uid}-mode`, 'Mode')}
						<PickerSelect value={choice?.mode ?? ''} options={modeOptions} label="Choose mode" width={400} onchange={pickMode}>
							{#snippet trigger(select)}
								<Tooltip text={select.selected?.label ?? ''} truncateOnly position="bottom" delay={400} wrapperClass="flex min-w-0">
									<button
										type="button"
										class="{controlClass(select.open)} {DOCK_CELL.mode} text-base font-medium"
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
											</span>
										<Icon name="chevron-down" className="h-[13px] w-[13px] flex-shrink-0 text-fg-subtle transition-transform {select.open ? 'rotate-180' : ''}" />
									</button>
								</Tooltip>
							{/snippet}
						</PickerSelect>
					</div>
				{/if}

				{#if choice}
					<ContextCut />
					<div class={DOCK_STEP.session} data-testid="editor-session-step" data-context-step>
						{@render stepLabel(`${uid}-session`, 'Session')}
						<SessionControl session={$session} actions={sessionActions} {fieldLabels} compact widthClass={sessionWidth} autosave={false} shortcut={false} labelId="{uid}-session">
							{#snippet trailing()}<EditorDefaultButton {taskId} {label} />{/snippet}
						</SessionControl>
					</div>
				{:else}
					<div class="flex flex-none items-center self-center px-3" data-testid="editor-default-button"><EditorDefaultButton {taskId} {label} /></div>
				{/if}

				{#if showFormulas}
					<ContextCut />
					<div class={DOCK_STEP.formula} data-testid="editor-formula" data-context-step>
						{@render stepLabel(`${uid}-formula`, 'Formula')}
						<PickerSelect value={formulaValue} options={formulaOptions} label="Choose formula" width={320} onchange={pickFormula}>
							{#snippet trigger(select)}
								<Tooltip text={select.selected?.label ?? ''} truncateOnly position="bottom" delay={400} wrapperClass="flex min-w-0">
									<button
										type="button"
										class="{controlClass(select.open)} {DOCK_CELL.formula} text-base font-medium"
										aria-haspopup="listbox"
										aria-expanded={select.open}
										aria-labelledby="{uid}-formula {uid}-formula-value"
										onclick={select.toggle}
										onkeydown={select.keydown}
									>
										<span class="ctx-valuerow">
											<Icon name="sliders" className="h-3.5 w-3.5 flex-shrink-0 text-fg-muted" />
											<span id="{uid}-formula-value" class="min-w-0 truncate {formulaValue === NO_FORMULA ? 'text-fg-muted' : ''}">{formulaValue === NO_FORMULA ? 'Formula' : (select.selected?.label ?? 'Formula')}</span>
										</span>
										<Icon name="chevron-down" className="h-[13px] w-[13px] flex-shrink-0 text-fg-subtle transition-transform {select.open ? 'rotate-180' : ''}" />
									</button>
								</Tooltip>
							{/snippet}
						</PickerSelect>
					</div>
				{/if}
			</div>
			<div class={DOCK_END} data-editor-pickers-end>
				{@render extras?.()}
				<EditorFormulas {taskId} />
				{@render changedMark?.()}
				{@render settingsButton?.()}
			</div>
		</div>
	{/if}
</div>

<SessionHost controller={sessionController} {dialogs} scripts={false} shortcut={false} />

<style>
	.dock-zone :global(.ctx-step) {
		min-height: 38px;
		margin-bottom: 4px;
	}

	.dock-zone :global(.ctx-select:not(.preset-picker-cta)) {
		height: 24px;
		line-height: 20px;
	}

	.dock-zone :global(.ctx-seg) {
		height: 24px;
		align-items: center;
	}

	.dock-zone :global(.session-actions) {
		position: relative;
		top: -8px;
	}

	.dock-end :global(.dock-ctl) {
		height: 24px;
		flex: none;
		display: flex;
		align-items: center;
	}

	.dock-end :global(.dock-btn) {
		flex: none;
		display: flex;
		align-items: center;
	}

	@container (max-width: 759px) {
		.dock-zone :global(.ctx-seg) {
			min-width: 0;
		}

		.dock-zone :global([data-context-step]) {
			flex-shrink: 1 !important;
		}

		.dock-zone :global([data-testid='preset-picker-trigger']) {
			min-width: 72px;
		}

		.dock-zone :global([data-testid='preset-header-mode'] button) {
			min-width: 48px;
		}

		.dock-zone :global(.default-label) {
			display: none;
		}

		.dock-zone :global([data-testid='default-button']) {
			width: 32px;
			padding: 0 !important;
		}
	}

	@container (max-width: 719px) {
		div.dock-zone {
			height: auto;
			flex-wrap: wrap;
		}

		.dock-pickers {
			flex: 1 1 100%;
			min-height: 48px;
		}

		.dock-zone :global(.ctx-step) {
			min-height: 44px;
		}

		.dock-end :global(.dock-btn button) {
			min-height: 40px;
		}

		.dock-end {
			flex: 0 0 auto;
			width: calc(100% + 24px);
			height: 48px;
			margin: 0 -12px;
			padding: 0 12px;
			justify-content: flex-end;
			align-items: center;
			border-top: 1px solid rgb(var(--line));
		}
	}
</style>
