<script lang="ts">
	import type { FieldLabels } from '$lib/session/sessionDrawerModel';
	import type { SessionPanelActions, SessionPanelState } from '$lib/session/sessionDialogs';
	import Icon from '$lib/components/Icon.svelte';
	import { IconButton, Spinner } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import SessionPanel from '$lib/components/session/SessionPanel.svelte';
	import { sideDrawer } from '$lib/stores/sideDrawer';
	import { shortcutLabels } from '$lib/stores/keybindings';

	let {
		session,
		actions,
		fieldLabels = {},
		layout = 'segment',
		compact = false,
		autosave = true,
		shortcut = true,
		labelId = undefined,
		widthClass = undefined
	}: {
		session: SessionPanelState;
		actions: SessionPanelActions;
		fieldLabels?: FieldLabels;
		layout?: 'segment' | 'sheet';
		compact?: boolean;
		autosave?: boolean;
		shortcut?: boolean;
		labelId?: string;
		widthClass?: string;
	} = $props();

	const uid = $props.id();

	let open = $state(false);
	let anchorEl = $state<HTMLElement>();

	let enabled = $derived(session.sessionControlsEnabled);
	let current = $derived(session.currentSession);
	let saving = $derived(session.isQuickSaving);
	let dirty = $derived(session.hasUnsavedChanges);

	let label = $derived(current?.name ?? (enabled ? 'No saved session' : 'Session unavailable'));
	let dotClass = $derived(
		saving ? 'bg-signal-solid' : !current ? 'bg-fg-subtle' : dirty ? 'bg-warning-solid' : 'bg-success-solid'
	);
	let saveState = $derived<'unavailable' | 'new' | 'saving' | 'dirty' | 'saved'>(
		!enabled ? 'unavailable' : !current ? 'new' : saving ? 'saving' : dirty ? 'dirty' : 'saved'
	);
	let saveLabel = $derived(
		{
			unavailable: 'Session save unavailable',
			new: 'Save as a new session',
			saving: 'Saving session',
			dirty: 'Save session',
			saved: 'Session saved'
		}[saveState]
	);
	let saveDisabled = $derived(saveState === 'unavailable' || saveState === 'saved');
	let segmentWidth = $derived(
		widthClass ??
			(current
			? `${compact ? 'min-w-[214px]' : 'min-w-[262px]'} max-w-[442px]`
			: `${compact ? 'min-w-[176px]' : 'min-w-[224px]'} max-w-[404px]`)
	);
	let reloadDisabled = $derived(!dirty || saving);
	let reloadTip = $derived(dirty ? 'Reload from last snapshot' : 'No unsaved changes to discard');
	let saveTip = $derived(saveState === 'saved' ? 'Saved' : saveState === 'unavailable' ? 'Nothing to save' : saveLabel);

	$effect(() => {
		if ($sideDrawer === 'formulas') open = false;
	});

	function toggle() {
		if (!enabled) return;
		open = !open;
		if (open) sideDrawer.open('sessions');
		else sideDrawer.close('sessions');
	}

	function close() {
		open = false;
		sideDrawer.close('sessions');
	}

	function reload() {
		if (!reloadDisabled) actions.reload();
	}

	function save() {
		if (saveDisabled) return;
		if (!current) actions.saveAs();
		else void actions.save();
	}
</script>

{#snippet saveButton(shape: string)}
	<Tooltip text={saveTip} kbd={saveDisabled || !shortcut ? undefined : $shortcutLabels['save_session']} position="bottom" delay={150} wrapperClass="flex flex-shrink-0">
		<button
			type="button"
			class="flex w-[78px] flex-shrink-0 items-center justify-center gap-1.5 transition-colors {shape} {saveDisabled
				? 'cursor-not-allowed font-medium text-fg-disabled'
				: saveState === 'saving'
					? 'font-medium text-fg-muted'
					: 'font-semibold text-accent'}"
			aria-label={saveLabel}
			aria-disabled={saveDisabled ? 'true' : undefined}
			data-testid="session-save"
			onclick={save}
		>
			{#if saveState === 'saving'}
				<Spinner size="sm" />
				<span>Saving</span>
			{:else}
				<Icon name={current ? 'save' : 'plus'} className="h-3.5 w-3.5" />
				<span>Save</span>
			{/if}
		</button>
	</Tooltip>
{/snippet}

{#if layout === 'sheet'}
	<div
		bind:this={anchorEl}
		class="flex h-[42px] min-w-0 items-stretch rounded border bg-surface-1 transition-colors {open
			? 'border-line-hover'
			: 'border-line-strong hover:border-line-hover'}"
	>
		<Tooltip text={current?.name ?? ''} truncateOnly position="bottom" delay={400} wrapperClass="flex min-w-0 flex-1">
			<button
				type="button"
				class="flex min-w-0 flex-1 items-center gap-2 rounded-l pl-3 pr-2.5 text-left transition-colors hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50"
				aria-label="Session"
				aria-haspopup="dialog"
				aria-expanded={open}
				disabled={!enabled}
				onclick={toggle}
			>
				<span class="relative flex h-6 w-6 flex-shrink-0 items-center justify-center rounded bg-surface-3 text-fg-muted">
					<Icon name="document" className="h-3.5 w-3.5" />
					<span class="status-dot absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full {dotClass}" aria-hidden="true"></span>
				</span>
				<span class="min-w-0 flex-1 truncate text-sm {current ? 'font-semibold text-fg' : 'font-medium text-fg-muted'}">{label}</span>
				<Icon name="chevron-down" className="h-4 w-4 flex-shrink-0 text-fg-subtle transition-transform {open ? 'rotate-180' : ''}" />
			</button>
		</Tooltip>
		{@render saveButton(`rounded-r border-l border-line-strong text-sm ${saveDisabled ? '' : 'hover:bg-surface-2'}`)}
	</div>
{:else}
	<div
		bind:this={anchorEl}
		class="ctx-seg flex {segmentWidth} shrink gap-1.5"
		data-testid="context-session"
	>
		<Tooltip text={current?.name ?? ''} truncateOnly position="bottom" delay={400} wrapperClass="flex min-w-0 flex-1">
			<button
				type="button"
				class="ctx-select ctx-select-session flex min-w-0 flex-1 items-center text-left text-base font-medium disabled:cursor-not-allowed disabled:opacity-50 {open ? 'is-open' : ''}"
				aria-label={labelId ? undefined : 'Session'}
				aria-labelledby={labelId ? `${labelId} ${uid}-value` : undefined}
				data-testid="session-trigger"
				aria-haspopup="dialog"
				aria-expanded={open}
				disabled={!enabled}
				onclick={toggle}
			>
				<span class="ctx-valuerow">
					<span class="h-[7px] w-[7px] flex-shrink-0 rounded-full {dotClass}" aria-hidden="true"></span>
					<span id="{uid}-value" class="min-w-0 flex-1 truncate {current ? 'text-fg' : 'text-fg-muted'}">{label}</span>
				</span>
				<Icon name="chevron-down" className="h-[13px] w-[13px] flex-shrink-0 text-fg-subtle transition-transform {open ? 'rotate-180' : ''}" />
			</button>
		</Tooltip>
		{@render saveButton(`h-8 rounded text-sm ${saveDisabled ? 'bg-surface-3/50' : 'bg-surface-3 hover:bg-line-hover'}`)}
		{#if current}
			<Tooltip text={reloadTip} position="bottom" delay={150} wrapperClass="flex flex-shrink-0">
				<IconButton
					icon="undo"
					label="Reload from last snapshot"
					variant="secondary"
					size="sm"
					class="!p-0 {reloadDisabled ? '!cursor-not-allowed !bg-surface-3/50 !text-fg-disabled' : ''}"
					aria-disabled={reloadDisabled ? 'true' : undefined}
					data-testid="session-reload"
					onclick={reload}
				/>
			</Tooltip>
		{/if}
	</div>
{/if}

{#if open}
	<SessionPanel {fieldLabels} {session} {actions} {autosave} anchor={anchorEl} fullscreen={layout === 'sheet'} onClose={close} />
{/if}

<style>
	.status-dot {
		box-shadow: 0 0 0 2px rgb(var(--surface-1));
	}
</style>
