<script lang="ts">
	import { goto } from '$app/navigation';
	import { Button, Badge, IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import {
		automationEditor,
		isDirty,
		canUndo,
		canRedo
	} from '$lib/stores/automationEditor';
	import { automationRuns } from '$lib/stores/automationRuns';
	import { toasts } from '$lib/stores/toast';
	import { api } from '$lib/services/api';
	import { downloadJson } from '$lib/utils/downloadJson';
	import { nodeDetail, toggleNodeDetail } from '$lib/automations/nodeDetail';

	function handleBack() {
		goto('/automations');
	}

	let automation = $derived($automationEditor.automation);
	let dirty = $derived($isDirty);
	let saving = $derived($automationEditor.saving);
	let validating = $derived($automationEditor.validating);
	let undoable = $derived($canUndo);
	let redoable = $derived($canRedo);
	let detail = $derived($nodeDetail);

	async function handleSave() {
		const ok = await automationEditor.save();
		if (ok) {
			toasts.success('Automation saved');
		} else {
			toasts.error($automationEditor.error || 'Failed to save automation');
		}
	}

	async function handleValidate() {
		const issues = await automationEditor.validate();
		const errors = issues.filter((i) => i.severity === 'error');
		if (errors.length === 0) {
			toasts.success(issues.length === 0 ? 'Graph is valid' : 'No blocking errors');
		} else {
			toasts.warning(`${errors.length} validation error${errors.length === 1 ? '' : 's'} found`);
		}
	}

	async function handleToggleEnabled() {
		if (!automation) return;
		await automationEditor.setEnabled(!automation.enabled);
	}

	async function handleRunNow() {
		if (!automation) return;
		try {
			const response = await import('$lib/services/api').then(({ api }) =>
				api.runAutomation(automation!.id)
			);
			if (response.success) {
				toasts.success('Run started');
				await automationRuns.loadRuns(automation.id);
			} else {
				toasts.error(response.error || 'Failed to start run');
			}
		} catch {
			toasts.error('Failed to start run');
		}
	}

	async function handleExport() {
		if (!automation) return;
		try {
			const response = await api.exportAutomation(automation.id);
			if (response.success && response.data) {
				downloadJson(`${automation.name}.json`, response.data);
			} else {
				toasts.error(response.message || response.error || 'Failed to export automation');
			}
		} catch {
			toasts.error('Failed to export automation');
		}
	}

	function handleUndo() {
		automationEditor.undo();
	}

	function handleRedo() {
		automationEditor.redo();
	}

	function handleAutoLayout() {
		automationEditor.applyAutoLayout();
	}

	function handleToggleDetail() {
		toggleNodeDetail(detail);
	}
</script>

<div class="h-header flex items-center justify-between gap-3 px-4 border-b border-line bg-surface-1 flex-shrink-0">
	<div class="min-w-0 flex items-center gap-2">
		<Tooltip text="Back to automations">
			<IconButton icon="chevron-left" label="Back to automations" onclick={handleBack} />
		</Tooltip>
		<h1 class="text-sm font-semibold text-fg truncate">{automation?.name ?? 'Automation'}</h1>
		{#if automation}
			<Badge variant={automation.enabled ? 'success' : 'neutral'} size="sm">
				{automation.enabled ? 'Enabled' : 'Disabled'}
			</Badge>
			{#if dirty}
				<Badge variant="warning" size="sm">Unsaved changes</Badge>
			{/if}
		{/if}
	</div>

	<div class="flex items-center gap-2 flex-shrink-0">
		<Tooltip text="Undo (Ctrl+Z)">
			<IconButton icon="undo" label="Undo" disabled={!undoable} onclick={handleUndo} />
		</Tooltip>
		<Tooltip text="Redo (Ctrl+Shift+Z)">
			<IconButton
				icon="undo"
				label="Redo"
				class="scale-x-[-1]"
				disabled={!redoable}
				onclick={handleRedo}
			/>
		</Tooltip>
		<Tooltip text="Auto-layout the graph">
			<IconButton icon="grid" label="Auto-layout" onclick={handleAutoLayout} />
		</Tooltip>
		<Tooltip text={detail === 'full' ? 'Switch to compact node cards' : 'Switch to full node cards'}>
			<IconButton
				icon="layers"
				label="Toggle node detail"
				active={detail === 'compact'}
				onclick={handleToggleDetail}
			/>
		</Tooltip>

		<div class="w-px h-5 bg-line mx-1" aria-hidden="true"></div>

		<Button variant="ghost" size="sm" icon="download" onclick={handleExport}>Export</Button>
		<Button variant="ghost" size="sm" icon="check" onclick={handleValidate} loading={validating}>
			Validate
		</Button>
		<Button variant="secondary" size="sm" icon="play" onclick={handleRunNow}>Run Now</Button>
		<Button variant="secondary" size="sm" onclick={handleToggleEnabled}>
			{automation?.enabled ? 'Disable' : 'Enable'}
		</Button>
		<Button
			variant="primary"
			size="sm"
			icon="check"
			onclick={handleSave}
			disabled={!dirty}
			loading={saving}
		>
			Save
		</Button>
	</div>
</div>
