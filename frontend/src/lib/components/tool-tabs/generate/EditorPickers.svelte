<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import PickerSelect from '$lib/components/preset/PickerSelect.svelte';
	import PresetPicker, { PRESET_PLACEHOLDER } from '$lib/components/preset/PresetPicker.svelte';
	import PresetThumbnail from '$lib/components/preset/PresetThumbnail.svelte';
	import ContextCut from '$lib/components/ui/ContextCut.svelte';
	import EditorNotices from './EditorNotices.svelte';
	import SessionControl from '$lib/components/session/SessionControl.svelte';
	import SessionSaveModal from '$lib/components/session/SessionSaveModal.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import { Spinner, Switch } from '$lib/components/ui';
	import { confirmDialog } from '$lib/stores/confirm';
	import { createSessionDialogs } from '$lib/session/sessionDialogs';
	import { loadFieldLabels } from '$lib/session/sessionFieldLabels';
	import type { FieldLabels } from '$lib/session/sessionDrawerModel';
	import { getGenerateController } from '$lib/tool-tabs/image/generate/context';
	import { createEditorSessionController } from '$lib/tool-tabs/image/generate/editorSessions';
	import { NO_FORMULA, formulaOptionsOf, modeOptionsOf, presetDisabledReasons, presetListOf } from '$lib/tool-tabs/image/generate/pickers';
	import { offerOf, sameChoice, setupName, sourceOf, type SetupChoice } from '$lib/tool-tabs/image/generate/setup';
	import { isChanged } from '$lib/tool-tabs/image/generate/tweaks';

	let { phone = false, taskId, label, extras, resultsStep, changedMark, settingsButton }: { phone?: boolean; taskId: string; label: string; extras?: Snippet; resultsStep?: Snippet; changedMark?: Snippet; settingsButton?: Snippet } = $props();

	const generate = getGenerateController()!;
	const ui = generate.ui;
	const uid = $props.id();

	const sessionController = createEditorSessionController(generate, () => taskId);
	const dialogs = createSessionDialogs(sessionController);
	const session = sessionController.state;
	const dialogState = dialogs.state;

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
	let isDefault = $derived.by(() => {
		void $ui;
		return generate.isDefault(taskId);
	});

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

{#snippet defaultSwitch()}
	<label class="flex min-w-0 items-center gap-2 text-sm text-fg-muted {phone ? '' : 'dock-ctl'}" data-picker-default>
		<Switch size="sm" checked={isDefault} disabled={!choice} label="Use as my default for {label}" onchange={(on) => void generate.setAsDefault(taskId, on)} />
		<span class="whitespace-nowrap">Use as my default</span>
	</label>
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
			{#if choice}
				<SessionControl session={$session} actions={dialogs.actions} {fieldLabels} layout="sheet" autosave={false} />
			{/if}
			{#if showFormulas}
				<PickerSelect value={formulaValue} options={formulaOptions} label="Choose formula" placeholder="Formula" onchange={pickFormula} />
			{/if}
			{@render defaultSwitch()}
		</div>
	{:else}
		<div class="dock-zone flex h-12 min-w-0 items-stretch" role="group" aria-label="Setup for {label}" data-testid="context-zone" style="--cut-stroke: var(--canvas)">
			<div class="ctx-step flex min-w-0 shrink pl-0 pr-4" data-context-step>
				{@render stepLabel(`${uid}-preset`, 'Preset')}
				<div class="flex min-w-0" data-testid="preset-header-picker">
					<PresetPicker bind:this={presetPicker} {presets} selectedPreset={choice?.preset_id ?? ''} disabled={nothingOffered} disabledReasons={reasons} recordRecent={false} on:select={(event) => pickPreset(event.detail)}>
						{#snippet trigger(picker)}
							<Tooltip text={picker.currentPreset?.name ?? ''} truncateOnly position="bottom" delay={400} wrapperClass="flex min-w-0">
								<button
									bind:this={presetButton}
									type="button"
									class="{controlClass(picker.expanded)} min-w-[88px] max-w-[200px] text-base {picker.currentPreset ? 'font-semibold' : 'font-medium'}"
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
				<div class="ctx-step flex min-w-0 shrink px-4" data-testid="preset-header-mode" data-context-step>
					{@render stepLabel(`${uid}-mode`, 'Mode')}
					<PickerSelect value={choice?.mode ?? ''} options={modeOptions} label="Choose mode" width={400} onchange={pickMode}>
						{#snippet trigger(select)}
							<Tooltip text={select.selected?.label ?? ''} truncateOnly position="bottom" delay={400} wrapperClass="flex min-w-0">
								<button
									type="button"
									class="{controlClass(select.open)} min-w-[72px] max-w-[160px] text-base font-medium"
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
				<div class="ctx-step flex min-w-0 shrink px-4" data-context-step>
					{@render stepLabel(`${uid}-session`, 'Session')}
					<SessionControl session={$session} actions={dialogs.actions} {fieldLabels} compact autosave={false} iconSave labelId="{uid}-session" />
				</div>
			{/if}

			{#if showFormulas}
				<ContextCut />
				<div class="ctx-step flex min-w-0 shrink px-4" data-testid="editor-formula" data-context-step>
					{@render stepLabel(`${uid}-formula`, 'Formula')}
					<PickerSelect value={formulaValue} options={formulaOptions} label="Choose formula" width={320} onchange={pickFormula}>
						{#snippet trigger(select)}
							<Tooltip text={select.selected?.label ?? ''} truncateOnly position="bottom" delay={400} wrapperClass="flex min-w-0">
								<button
									type="button"
									class="{controlClass(select.open)} min-w-[72px] max-w-[160px] text-base font-medium"
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
			<div class="dock-end ml-auto flex flex-none items-end gap-4 self-stretch whitespace-nowrap pl-5" data-editor-pickers-end>
				{@render extras?.()}
				{@render resultsStep?.()}
				{@render changedMark?.()}
				{@render defaultSwitch()}
				{@render settingsButton?.()}
			</div>
		</div>
	{/if}
</div>

<SessionSaveModal
	isOpen={$dialogState.saveOpen}
	mode="rename"
	bind:sessionName={$dialogState.name}
	nameError={$session.nameError}
	error={$session.error}
	isSaving={$session.isSaving}
	on:close={dialogs.close}
	on:confirm={() => dialogs.confirmSave(false)}
/>

<SessionSaveModal
	isOpen={$dialogState.saveAsOpen}
	mode="save-as"
	bind:sessionName={$dialogState.name}
	nameError={$session.nameError}
	error={$session.error}
	isSaving={$session.isSaving}
	on:close={dialogs.close}
	on:confirm={() => dialogs.confirmSave(true)}
/>

<ConfirmModal
	isOpen={$dialogState.deleteOpen}
	title="Delete Session"
	message={`Are you sure you want to delete "${$session.currentSession?.name}"? This action cannot be undone.`}
	variant="danger"
	busy={$session.isSessionLoading}
	on:confirm={dialogs.confirmDelete}
	on:cancel={dialogs.close}
/>

<ConfirmModal
	isOpen={$dialogState.reloadOpen}
	title="Reload last snapshot"
	message={`Discard your changes and reload the last snapshot of "${$session.currentSession?.name}"?`}
	confirmLabel="Reload"
	variant="warning"
	busy={$session.isSessionLoading || $session.isQuickSaving || $session.isSaving}
	on:confirm={dialogs.confirmReload}
	on:cancel={dialogs.close}
/>

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

	.dock-end :global(.dock-ctl) {
		height: 24px;
		margin-bottom: 4px;
		flex: none;
		display: flex;
		align-items: center;
	}

	@container (max-width: 759px) {
		.dock-zone :global(.ctx-seg) {
			min-width: 0;
		}
	}

	@container (max-width: 719px) {
		div.dock-zone {
			height: auto;
			flex-wrap: wrap;
		}

		.dock-zone > :global(.ctx-step) {
			flex: 1 1 0;
			min-height: 44px;
		}

		.dock-end {
			flex: 0 0 auto;
			width: calc(100% + 24px);
			height: 48px;
			margin: 0 -12px;
			padding: 0 12px;
			justify-content: flex-end;
			border-top: 1px solid rgb(var(--line));
		}
	}
</style>
