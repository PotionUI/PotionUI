<script lang="ts">
	import { onMount, onDestroy, untrack } from 'svelte';
	import { SvelteFlowProvider } from '@xyflow/svelte';
	import { Button, IconButton, Spinner, Switch } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { DetailHeader, DetailTabs, DetailBody, DetailFooter } from '$lib/components/detail';
	import RunStatusBadge from '$lib/components/automation/RunStatusBadge.svelte';
	import { adminSectionIcon } from '../../adminSections';
	import { api } from '$lib/services/api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import { downloadJson } from '$lib/utils/downloadJson';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { automationEditor, isDirty } from '$lib/stores/automationEditor';
	import { automationNodeTypes } from '$lib/stores/automationNodeTypes';
	import { automationRuns } from '$lib/stores/automationRuns';
	import { automationRunsWebSocket } from '$lib/services/automationRunsWebsocket';
	import { triggerLabel } from './automationTriggerLabel';
	import Toolbar from '../../../automations/[id]/components/Toolbar.svelte';
	import NodePalette from '../../../automations/[id]/components/NodePalette.svelte';
	import Canvas from '../../../automations/[id]/components/Canvas.svelte';
	import Inspector from '../../../automations/[id]/components/Inspector.svelte';
	import RunHistoryPanel from '../../../automations/[id]/components/RunHistoryPanel.svelte';
	import ValidationErrors from '../../../automations/[id]/components/ValidationErrors.svelte';
	import type { Automation } from '$lib/types/automations';

	type DetailTab = 'editor' | 'history';

	let {
		automationId,
		onBack,
		onDeleted,
		onAutomationChange
	}: {
		automationId: string;
		onBack: () => void;
		onDeleted: (id: string) => void;
		onAutomationChange: (automation: Automation) => void;
	} = $props();

	let detailTab = $state<DetailTab>('editor');
	let saving = $state(false);
	let discarding = $state(false);
	let toggling = $state(false);
	let running = $state(false);
	let exporting = $state(false);
	let deleting = $state(false);
	let overflowOpen = $state(false);
	let overflowEl: HTMLDivElement | undefined = $state();

	function closeOverflow() {
		overflowOpen = false;
	}

	function handleWindowClick(event: MouseEvent) {
		const target = event.target as Element | null;
		if (target?.closest('[role="dialog"], [role="alertdialog"], [aria-label="Close modal"]')) return;
		if (overflowOpen && overflowEl && !overflowEl.contains(event.target as Node)) overflowOpen = false;
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') overflowOpen = false;
	}

	let editorState = $derived($automationEditor);
	let automation = $derived(editorState.automation);
	let dirty = $derived($isDirty);
	let runs = $derived($automationRuns.runs);

	$effect(() => {
		const current = automation;
		if (current) untrack(() => onAutomationChange(current));
	});

	onMount(async () => {
		await Promise.all([
			automationNodeTypes.load(),
			automationEditor.load(automationId),
			automationRuns.loadRuns(automationId)
		]);
		automationRunsWebSocket.connect();
	});

	onDestroy(() => {
		automationRunsWebSocket.disconnect();
		automationEditor.reset();
		automationRuns.reset();
	});

	async function handleSave() {
		saving = true;
		try {
			const ok = await automationEditor.save();
			if (ok) toasts.success('Automation saved');
			else toasts.error($automationEditor.error || 'Failed to save automation');
		} finally {
			saving = false;
		}
	}

	async function handleDiscard() {
		discarding = true;
		try {
			await automationEditor.load(automationId);
			toasts.success('Reverted to the saved version');
		} finally {
			discarding = false;
		}
	}

	async function handleToggleEnabled() {
		if (!automation) return;
		toggling = true;
		try {
			await automationEditor.setEnabled(!automation.enabled);
		} finally {
			toggling = false;
		}
	}

	async function handleRunNow() {
		if (!automation) return;
		running = true;
		try {
			const response = await api.runAutomation(automation.id);
			if (response.success) {
				toasts.success('Run started');
				await automationRuns.loadRuns(automation.id);
			} else {
				toasts.error(response.error || 'Failed to start run');
			}
		} catch {
			toasts.error('Failed to start run');
		} finally {
			running = false;
		}
	}

	async function handleExport() {
		if (!automation) return;
		closeOverflow();
		exporting = true;
		try {
			const response = await api.exportAutomation(automation.id);
			if (response.success && response.data) {
				downloadJson(`${automation.name}.json`, response.data);
			} else {
				toasts.error(response.message || response.error || 'Failed to export automation');
			}
		} catch {
			toasts.error('Failed to export automation');
		} finally {
			exporting = false;
		}
	}

	async function handleDelete() {
		closeOverflow();
		if (!automation) return;
		if (
			!(await confirmDialog({
				title: `Delete "${automation.name}"?`,
				message: 'This cannot be undone.',
				variant: 'danger'
			}))
		)
			return;
		deleting = true;
		try {
			const response = await api.deleteAutomation(automation.id);
			if (response.success) {
				onDeleted(automation.id);
				onBack();
			} else {
				toasts.error(response.error || 'Failed to delete automation');
			}
		} catch {
			toasts.error('Failed to delete automation');
		} finally {
			deleting = false;
		}
	}
</script>

<svelte:window onclick={handleWindowClick} onkeydown={handleWindowKeydown} />

<div class="flex h-full flex-col">
	{#if !editorState.loaded}
		<div class="flex flex-1 items-center justify-center">
			<Spinner size="lg" />
		</div>
	{:else if editorState.error || !automation}
		<div class="flex flex-1 items-center justify-center">
			<p class="text-sm text-danger">{editorState.error || 'Automation not found'}</p>
		</div>
	{:else}
		<DetailHeader title={automation.name} icon={adminSectionIcon('automations')} backLabel="Automations" onBack={onBack}>
			{#snippet chips()}
				{#if automation.last_run_status}
					<RunStatusBadge status={automation.last_run_status} />
				{/if}
			{/snippet}
			{#snippet subtitle()}
				<span>{triggerLabel(automation.graph, $automationNodeTypes)}</span>
				<span aria-hidden="true">·</span>
				{#if automation.last_run_at}
					<Tooltip text={new Date(automation.last_run_at).toLocaleString()}>
						<span>Last run {timeAgo(automation.last_run_at)}</span>
					</Tooltip>
				{:else}
					<span>Never run</span>
				{/if}
			{/snippet}
			{#snippet enabledSwitch()}
				<Switch
					label={automation.enabled ? 'Disable automation' : 'Enable automation'}
					checked={automation.enabled}
					busy={toggling}
					onchange={handleToggleEnabled}
				/>
			{/snippet}
			{#snippet actions()}
				<Button variant="primary" size="sm" icon="play" onclick={handleRunNow} loading={running}>
					Run now
				</Button>
				<div class="relative" bind:this={overflowEl}>
					<Tooltip text="More actions">
						<IconButton
							icon="more"
							label="More actions"
							ariaExpanded={overflowOpen}
							active={overflowOpen}
							onclick={() => (overflowOpen = !overflowOpen)}
						/>
					</Tooltip>
					{#if overflowOpen}
						<div
							class="absolute right-0 top-[calc(100%+6px)] z-40 min-w-[180px] overflow-hidden rounded-xl border border-line-strong bg-surface-2 py-1 shadow-floating"
							role="menu"
						>
							<button
								type="button"
								role="menuitem"
								class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-fg hover:bg-surface-3 disabled:opacity-50"
								onclick={handleExport}
								disabled={exporting}
							>
								<Icon name="download" className="w-3.5 h-3.5" />
								Export
							</button>
							<button
								type="button"
								role="menuitem"
								class="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-danger hover:bg-danger/10 disabled:opacity-50"
								onclick={handleDelete}
								disabled={deleting}
							>
								<Icon name="trash" className="w-3.5 h-3.5" />
								Delete
							</button>
						</div>
					{/if}
				</div>
			{/snippet}
		</DetailHeader>

		<DetailTabs
			tabs={[
				{ id: 'editor', label: 'Editor', icon: 'sliders' },
				{ id: 'history', label: 'Run history', icon: 'clock', count: runs.length || undefined }
			]}
			active={detailTab}
			onSelect={(id) => (detailTab = id as DetailTab)}
			ariaLabel="Automation details"
		/>

		{#if detailTab === 'editor'}
			<div class="flex flex-1 min-h-0 flex-col items-center justify-center p-6 text-center sm:hidden">
				<Icon name="warning" className="w-6 h-6 text-fg-subtle mb-2" />
				<p class="text-sm text-fg-muted">Open on a larger screen to edit this flow.</p>
			</div>
			<div class="hidden sm:flex flex-1 min-h-0 flex-col overflow-hidden">
				<SvelteFlowProvider>
					<Toolbar />
					<div class="flex flex-1 min-h-0 overflow-hidden">
						<NodePalette />
						<Canvas />
						<Inspector />
					</div>
				</SvelteFlowProvider>
				<ValidationErrors issues={editorState.validationIssues} />
			</div>
			<div class="hidden sm:block">
				<DetailFooter
					dirtyCount={dirty ? 1 : 0}
					mode="edit"
					saving={saving || discarding}
					canSave={true}
					onSave={handleSave}
					onDiscard={handleDiscard}
				/>
			</div>
		{:else}
			<DetailBody>
				<RunHistoryPanel {automationId} />
			</DetailBody>
		{/if}
	{/if}
</div>
